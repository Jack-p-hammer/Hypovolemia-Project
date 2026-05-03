import asyncio
import threading
import tkinter as tk
import csv
import os
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from bleak import BleakScanner, BleakClient
from datetime import datetime
from collections import deque


# =============================================================================
# SETTINGS  —  change these if the device name, UUID, or display window changes
# =============================================================================

# Must match the name broadcast by the Arduino
DEVICE_NAME = "PPG_Forearm"

# Must match CHAR_UUID in BluetoothTest.ino
CHAR_UUID = "abcdefab-1234-1234-1234-abcdefabcdef"

# Where the CSV log gets saved (defaults to the Documents folder in the user's home directory)
OUTPUT_DIR = os.path.expanduser("~/Documents")

# How many seconds of PPG signal to show on the live graph at once
GRAPH_WINDOW_SECONDS = 10

# Maximum number of data points kept in memory for each signal
MAX_POINTS = 5000

# Hypovolemia score threshold — scores at or above this are shown as NORMAL.
# The score is based on PPG signal amplitude (see hypovolemia_score() below).
HYPO_NORMAL_THRESHOLD = 80


# =============================================================================
# COLORS  —  edit here to change the look of the dashboard
# =============================================================================

BG_MAIN    = "#e8eef4"   # outer background
BG_CARD    = "#f2f6fa"   # slightly lighter background used inside cards
WHITE      = "#ffffff"   # card face color
BORDER     = "#c8d4e0"   # card border color
BLUE       = "#1a6fa8"   # neck PPG line and header bar
RED        = "#c0392b"   # arm PPG line and CRITICAL status
TEXT       = "#1a2535"   # primary text
TEXT_MUTED = "#7a8a9a"   # secondary / label text
GREEN      = "#27ae60"   # NORMAL status
AMBER      = "#e67e22"   # LOW status and trend indicator
FONT_MONO  = "Courier New"


# =============================================================================
# SHARED DATA BUFFERS
# These are written by the Bluetooth background thread and read by the UI thread.
# deque(maxlen=N) automatically drops the oldest value once it hits N entries.
# =============================================================================

time_data   = deque(maxlen=MAX_POINTS)   # elapsed seconds since first sample
neck_data   = deque(maxlen=MAX_POINTS)   # neck PPG signal values
arm_data    = deque(maxlen=MAX_POINTS)   # arm PPG signal values
t_neck_data = deque(maxlen=500)          # neck temperature readings
t_arm_data  = deque(maxlen=500)          # arm temperature readings

# Holds the single most recent value for each channel, used by the UI widgets
latest = {
    "neck":      90.0,
    "arm":       90.0,
    "t_neck":    98.4,
    "t_arm":     95.2,
    "connected": False,
    "packets":   0,
}

# Set when the first sample arrives; used to compute elapsed time for each sample
start_time   = None
sample_count = 0

# CSV file handles — opened in Dashboard._open_csv()
csv_file   = None
csv_writer = None


# =============================================================================
# DATA PARSING
# The Arduino sends batches of samples over BLE as a plain text string:
#   "neck,arm,t_neck,t_arm;neck,arm,t_neck,t_arm;..."
# Each semicolon-delimited chunk is one sample with four comma-separated values.
# =============================================================================

def parse_batch(raw_bytes: bytes):
    """Decode a raw BLE notification and store each sample in the data buffers."""
    global start_time, sample_count, csv_writer, csv_file

    text = raw_bytes.decode("utf-8", errors="ignore").strip()

    for sample_str in text.split(";"):
        sample_str = sample_str.strip()
        if not sample_str:
            continue  # skip the empty string after the trailing semicolon

        parts = sample_str.split(",")
        if len(parts) < 4:
            continue  # malformed sample, skip it

        try:
            # Firmware format: IR_neck, RED_neck, T_neck, IR_arm, RED_arm, T_arm
            neck   = float(parts[0])   # IR_neck  — used as neck PPG signal
            t_neck = float(parts[2])   # T_neck
            arm    = float(parts[3])   # IR_arm   — used as arm PPG signal
            t_arm  = float(parts[5]) if len(parts) > 5 else latest["t_arm"]  # T_arm
        except ValueError:
            continue  # couldn't parse the numbers, skip this sample

        # Set the start time on the very first sample so timestamps begin at 0
        if start_time is None:
            start_time = datetime.now()

        elapsed_seconds = (datetime.now() - start_time).total_seconds()

        # Add to rolling buffers
        time_data.append(elapsed_seconds)
        neck_data.append(neck)
        arm_data.append(arm)
        t_neck_data.append(t_neck)
        t_arm_data.append(t_arm)

        # Update the "latest value" snapshot used by the UI widgets
        latest["neck"]   = neck
        latest["arm"]    = arm
        latest["t_neck"] = t_neck
        latest["t_arm"]  = t_arm
        latest["packets"] += 1
        sample_count += 1

        # Write the sample to the CSV log immediately
        if csv_writer:
            csv_writer.writerow([f"{elapsed_seconds:.4f}", neck, arm, t_neck, t_arm])
            csv_file.flush()


# =============================================================================
# BLUETOOTH
# BLE uses asyncio, which has to run in its own thread so it doesn't block
# the tkinter UI loop.  The notification handler is called every time the
# Arduino sends a new batch.
# =============================================================================

def ble_notification_handler(sender, raw_bytes):
    """Called automatically by bleak each time the Arduino sends new data."""
    parse_batch(raw_bytes)


async def ble_connect_and_stream(update_status):
    """
    Scan for the device, connect, and stream data until disconnected.
    update_status() is a callback that updates the connection label in the UI.
    """
    while True:
        update_status("scanning for " + DEVICE_NAME + "...")

        # Use discover() with return_adv=True so we check both the advertisement
        # packet and the scan response — the ESP32 puts its name in the scan
        # response, which plain d.name can miss on Windows.
        try:
            found = await BleakScanner.discover(timeout=5.0, return_adv=True)
            device = next(
                (d for d, adv in found.values()
                 if (d.name or adv.local_name or "") == DEVICE_NAME),
                None
            )
        except Exception as e:
            update_status(f"scan error — retrying...")
            await asyncio.sleep(3.0)
            continue

        if device is None:
            update_status("not found — retrying...")
            await asyncio.sleep(3.0)
            continue

        update_status(f"connecting to {device.address}...")

        try:
            async with BleakClient(device, timeout=10.0) as client:
                latest["connected"] = True
                update_status("connected")

                try:
                    await client.request_mtu(512)
                except Exception:
                    pass  # Windows manages MTU automatically

                await client.start_notify(CHAR_UUID, ble_notification_handler)

                while client.is_connected:
                    await asyncio.sleep(0.5)

        except Exception as e:
            pass

        latest["connected"] = False
        update_status("disconnected — retrying...")
        await asyncio.sleep(3.0)


def start_ble_thread(update_status):
    """Spin up the asyncio BLE loop in a background daemon thread."""
    def run():
        # Explicitly create a new event loop for this thread.
        # asyncio.run() can be unreliable inside non-main threads on Windows.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(ble_connect_and_stream(update_status))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()


# =============================================================================
# HYPOVOLEMIA SCORE
# This is a simple proxy for perfusion index based on PPG signal amplitude.
# It looks at the last 100 samples, computes peak-to-peak amplitude, and
# scales that to a 0-100 score.  Adjust the multiplier (currently 20) to
# recalibrate what counts as "normal" amplitude for your sensor.
# =============================================================================

def hypovolemia_score(signal_deque):
    """Return a 0-100 perfusion score based on the amplitude of recent PPG data."""
    if len(signal_deque) < 10:
        return 50  # not enough data yet, return a neutral value

    recent_samples = list(signal_deque)[-100:]
    amplitude = max(recent_samples) - min(recent_samples) # random logic
    score = min(100, int(amplitude * 20))
    return score


# =============================================================================
# DASHBOARD  —  the main UI window
# =============================================================================

class Dashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("BT-SENSOR  ·  DATA LIVE STREAM")
        self.root.geometry("1100x720")
        self.root.configure(bg=BG_MAIN)
        self.root.resizable(True, True)

        self._open_csv()
        self._build_ui()
        self._start_ble()

    # -------------------------------------------------------------------------
    # CSV LOGGING
    # -------------------------------------------------------------------------

    def _open_csv(self):
        """Create a timestamped CSV file on the Desktop and write the header row."""
        global csv_file, csv_writer
        timestamp = datetime.now().strftime("%d-%b-%Y_%H-%M-%S")
        filepath = os.path.join(OUTPUT_DIR, f"ppg_dashboard_{timestamp}.csv")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        csv_file = open(filepath, "w", newline="")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(["timestamp_s", "Neck", "Arm", "T_Neck", "T_Arm"])
        csv_file.flush()

    # -------------------------------------------------------------------------
    # UI LAYOUT
    # -------------------------------------------------------------------------

    def _build_ui(self):
        """Assemble the full dashboard: header bar, four data cards, footer."""

        # --- Header bar across the top ---
        header = tk.Frame(self.root, bg=BLUE, height=44)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(header, text="  BT-SENSOR  ·  DATA LIVE STREAM",
                 bg=BLUE, fg="white",
                 font=(FONT_MONO, 12, "bold")).pack(side="left", pady=10)

        # Connection status label (updated by BLE thread callbacks)
        self.conn_var = tk.StringVar(value="● SEARCHING")
        self.conn_lbl = tk.Label(header, textvariable=self.conn_var,
                                 bg=BLUE, fg="#aaccee",
                                 font=(FONT_MONO, 10))
        self.conn_lbl.pack(side="right", padx=16)

        # Live UTC clock
        self.clock_var = tk.StringVar()
        tk.Label(header, textvariable=self.clock_var,
                 bg=BLUE, fg="#aaccee",
                 font=(FONT_MONO, 10)).pack(side="right", padx=8)
        self._tick_clock()

        # --- 2x2 grid of cards in the main area ---
        grid = tk.Frame(self.root, bg=BG_MAIN)
        grid.pack(fill="both", expand=True, padx=12, pady=10)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        grid.rowconfigure(0, weight=0)
        grid.rowconfigure(1, weight=1)

        self._build_temp_card(grid, row=0, col=0)       # top-left:  temperatures
        self._build_feedback_card(grid, row=0, col=1)   # top-right: delta-T feedback
        self._build_ppg_card(grid, row=1, col=0)        # bottom-left:  PPG waveforms
        self._build_hypo_card(grid, row=1, col=1)       # bottom-right: hypovolemia scores

        # --- Footer strip along the bottom ---
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x")
        footer = tk.Frame(self.root, bg=BG_CARD)
        footer.pack(fill="x")
        tk.Label(footer, text="  DEVICE: ARD-BT-001  ·  PROTO v0.1",
                 bg=BG_CARD, fg=TEXT_MUTED,
                 font=(FONT_MONO, 9)).pack(side="left", pady=4)
        self.sample_var = tk.StringVar(value="samples: 0")
        tk.Label(footer, textvariable=self.sample_var,
                 bg=BG_CARD, fg=TEXT_MUTED,
                 font=(FONT_MONO, 9)).pack(side="right", padx=12, pady=4)

        # Kick off the periodic UI refresh
        self.root.after(100, self._update_ui)

    def _make_card(self, parent, title, row, col, rowspan=1):
        """
        Helper that builds a titled card widget and returns the inner frame.
        All four dashboard sections use this same card style.
        """
        outer = tk.Frame(parent, bg=BG_MAIN)
        outer.grid(row=row, column=col, rowspan=rowspan,
                   padx=6, pady=6, sticky="nsew")

        tk.Label(outer, text=title,
                 bg=BG_MAIN, fg=TEXT_MUTED,
                 font=(FONT_MONO, 8)).pack(anchor="w", padx=2, pady=(0, 3))

        inner = tk.Frame(outer, bg=WHITE,
                         highlightbackground=BORDER,
                         highlightthickness=1)
        inner.pack(fill="both", expand=True)
        return inner

    # -------------------------------------------------------------------------
    # CARD: TEMPERATURE
    # -------------------------------------------------------------------------

    def _build_temp_card(self, parent, row, col):
        """Two rows showing the live neck and arm temperatures in degrees F."""
        card = self._make_card(parent, "TEMPERATURE", row, col)

        self.t_neck_var = tk.StringVar(value="98.4")
        self.t_arm_var  = tk.StringVar(value="95.2")

        sensors = [
            ("TNeck", self.t_neck_var),
            ("TArm",  self.t_arm_var),
        ]

        for label_text, value_var in sensors:
            row_frame = tk.Frame(card, bg=WHITE,
                                 highlightbackground=BORDER,
                                 highlightthickness=1)
            row_frame.pack(fill="x", padx=16, pady=8)

            tk.Label(row_frame, text=label_text,
                     bg=WHITE, fg=TEXT_MUTED,
                     font=(FONT_MONO, 10)).pack(side="left", padx=12, pady=10)

            tk.Label(row_frame, textvariable=value_var,
                     bg=WHITE, fg=TEXT,
                     font=(FONT_MONO, 22, "bold")).pack(side="left", padx=4)

            tk.Label(row_frame, text="F",
                     bg=WHITE, fg=TEXT_MUTED,
                     font=(FONT_MONO, 11)).pack(side="left")

    # -------------------------------------------------------------------------
    # CARD: DELTA-T FEEDBACK
    # -------------------------------------------------------------------------

    def _build_feedback_card(self, parent, row, col):
        """
        Shows the temperature difference between neck and arm (dT = T_neck - T_arm),
        plus a trend indicator (Rising / Stable / Falling) based on recent neck temperature.
        """
        card = self._make_card(parent, "FEEDBACK", row, col)

        tk.Label(card, text="TEMPERATURE",
                 bg=WHITE, fg=TEXT_MUTED,
                 font=(FONT_MONO, 9)).pack(anchor="w", padx=16, pady=(14, 2))

        self.delta_var = tk.StringVar(value="dT:  —")
        tk.Label(card, textvariable=self.delta_var,
                 bg=WHITE, fg=AMBER,
                 font=(FONT_MONO, 18, "bold")).pack(anchor="w", padx=16, pady=2)

        self.trend_var = tk.StringVar(value="dT Trend:  —")
        self.trend_lbl = tk.Label(card, textvariable=self.trend_var,
                                  bg=WHITE, fg=AMBER,
                                  font=(FONT_MONO, 13))
        self.trend_lbl.pack(anchor="w", padx=16, pady=(0, 12))

    # -------------------------------------------------------------------------
    # CARD: PPG WAVEFORMS
    # -------------------------------------------------------------------------

    def _build_ppg_card(self, parent, row, col):
        """
        Live scrolling graph of the raw neck and arm PPG signals.
        The graph shows the last GRAPH_WINDOW_SECONDS seconds of data.
        """
        card = self._make_card(parent, "PPG WAVEFORM", row, col)

        # Create a figure with two vertically stacked subplots sharing the x-axis
        self.fig_ppg, (self.ax_neck, self.ax_arm) = plt.subplots(
            2, 1, figsize=(5, 3.2), sharex=True
        )
        self.fig_ppg.patch.set_facecolor(WHITE)
        self.fig_ppg.subplots_adjust(hspace=0.1, left=0.08, right=0.97, top=0.93, bottom=0.12)

        # Style both axes the same way
        for ax, label in [(self.ax_neck, "Neck"), (self.ax_arm, "Arm")]:
            ax.set_facecolor(BG_CARD)
            ax.tick_params(colors=TEXT_MUTED, labelsize=7, length=2)
            for spine in ax.spines.values():
                spine.set_edgecolor(BORDER)
                spine.set_linewidth(0.5)
            ax.grid(True, color=BORDER, linewidth=0.3, linestyle="--")
            ax.set_ylabel(label, color=TEXT_MUTED, fontsize=8, fontfamily="monospace")

        self.ax_arm.set_xlabel("time (s)", color=TEXT_MUTED, fontsize=8, fontfamily="monospace")

        # The actual plot lines — data is updated dynamically in _update_plot()
        self.line_neck, = self.ax_neck.plot([], [], color=BLUE, linewidth=1.0)
        self.line_arm,  = self.ax_arm.plot([],  [], color=RED,  linewidth=1.0)

        canvas = FigureCanvasTkAgg(self.fig_ppg, master=card)
        canvas.get_tk_widget().configure(bg=WHITE, highlightthickness=0)
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=2, pady=2)
        self.ppg_canvas = canvas

        # FuncAnimation calls _update_plot() every 100 ms to redraw the waveforms
        self.ani = animation.FuncAnimation(
            self.fig_ppg, self._update_plot,
            interval=100, blit=False, cache_frame_data=False
        )

    # -------------------------------------------------------------------------
    # CARD: HYPOVOLEMIA SCORES
    # -------------------------------------------------------------------------

    def _build_hypo_card(self, parent, row, col):
        """
        Two side-by-side score boxes showing the hypovolemia indicator for
        neck and arm.  Each box has a large numeric score and a status label
        (NORMAL / LOW / CRITICAL) that changes color based on the threshold.
        """
        card = self._make_card(parent, "HYPOVOLEMIA INDICATOR", row, col)

        container = tk.Frame(card, bg=WHITE)
        container.pack(fill="both", expand=True, padx=12, pady=12)
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)

        # StringVars are updated by _update_ui() every 200 ms
        self.hypo_neck_score = tk.StringVar(value="—")
        self.hypo_neck_label = tk.StringVar(value="—")
        self.hypo_arm_score  = tk.StringVar(value="—")
        self.hypo_arm_label  = tk.StringVar(value="—")

        panels = [
            (0, "NECK", self.hypo_neck_score, self.hypo_neck_label),
            (1, "ARM",  self.hypo_arm_score,  self.hypo_arm_label),
        ]

        for column_index, title, score_var, label_var in panels:
            box = tk.Frame(container, bg=BG_CARD,
                           highlightbackground=BORDER, highlightthickness=1)
            box.grid(row=0, column=column_index, padx=6, pady=4, sticky="nsew")

            tk.Label(box, text=title,
                     bg=BG_CARD, fg=TEXT_MUTED,
                     font=(FONT_MONO, 9)).pack(pady=(10, 4))

            score_lbl = tk.Label(box, textvariable=score_var,
                                 bg=BG_CARD, fg=GREEN,
                                 font=(FONT_MONO, 32, "bold"))
            score_lbl.pack()

            status_lbl = tk.Label(box, textvariable=label_var,
                                  bg=BG_CARD, fg=TEXT_MUTED,
                                  font=(FONT_MONO, 9))
            status_lbl.pack(pady=(0, 10))

            # Save references so _update_ui() can change the text color
            if column_index == 0:
                self._hypo_neck_score_lbl  = score_lbl
                self._hypo_neck_status_lbl = status_lbl
            else:
                self._hypo_arm_score_lbl   = score_lbl
                self._hypo_arm_status_lbl  = status_lbl

    # -------------------------------------------------------------------------
    # PERIODIC UPDATES
    # -------------------------------------------------------------------------

    def _update_plot(self, frame):
        """
        Called every 100 ms by FuncAnimation to redraw the PPG waveforms.
        Only the last GRAPH_WINDOW_SECONDS of data is shown.
        """
        if not time_data:
            return

        all_times = list(time_data)
        all_neck  = list(neck_data)
        all_arm   = list(arm_data)

        t_max = all_times[-1]
        t_min = max(0, t_max - GRAPH_WINDOW_SECONDS)

        # Filter to only the samples inside the visible time window
        neck_window = [(t, v) for t, v in zip(all_times, all_neck) if t >= t_min]
        arm_window  = [(t, v) for t, v in zip(all_times, all_arm)  if t >= t_min]

        if neck_window:
            xs, ys = zip(*neck_window)
            self.line_neck.set_data(xs, ys)
        if arm_window:
            xs, ys = zip(*arm_window)
            self.line_arm.set_data(xs, ys)

        for ax in (self.ax_neck, self.ax_arm):
            ax.set_xlim(t_min, t_max + 0.1)
            try:
                ax.relim()
                ax.autoscale_view(scalex=False, scaley=True)
            except Exception:
                pass

    def _update_ui(self):
        """
        Called every 200 ms to refresh all the text widgets on the dashboard.
        Covers temperatures, delta-T, trend, hypovolemia scores, and connection state.
        """
        t_neck = latest["t_neck"]
        t_arm  = latest["t_arm"]

        # Update temperature displays
        self.t_neck_var.set(f"{t_neck:.1f}")
        self.t_arm_var.set(f"{t_arm:.1f}")

        # Delta-T: difference between neck and arm temperature
        delta = t_neck - t_arm
        self.delta_var.set(f"dT:  {delta:.1f}")

        # Trend: compare the average of the last 10 neck samples to samples from
        # 40-50 readings ago.  A positive diff means the temperature is rising.
        if len(t_neck_data) > 50:
            recent_avg = sum(list(t_neck_data)[-10:])    / 10
            older_avg  = sum(list(t_neck_data)[-50:-40]) / 10
            diff = recent_avg - older_avg

            if diff > 0.05:
                trend_text, trend_color = "->  Rising",  RED
            elif diff < -0.05:
                trend_text, trend_color = "->  Falling", BLUE
            else:
                trend_text, trend_color = "->  Stable",  AMBER
        else:
            trend_text, trend_color = "->  Waiting...", TEXT_MUTED

        self.trend_var.set(f"dT Trend:  {trend_text}")
        self.trend_lbl.configure(fg=trend_color)

        # Hypovolemia scores
        neck_score = hypovolemia_score(neck_data)
        arm_score  = hypovolemia_score(arm_data)

        def score_to_status(score):
            """Map a numeric score to a (status label, color) pair."""
            if score >= HYPO_NORMAL_THRESHOLD:
                return "NORMAL", GREEN
            elif score >= 40:
                return "LOW", AMBER
            else:
                return "CRITICAL", RED

        neck_label, neck_color = score_to_status(neck_score)
        arm_label,  arm_color  = score_to_status(arm_score)

        self.hypo_neck_score.set(str(neck_score))
        self.hypo_neck_label.set(neck_label)
        self._hypo_neck_score_lbl.configure(fg=neck_color)
        self._hypo_neck_status_lbl.configure(fg=neck_color)

        self.hypo_arm_score.set(str(arm_score))
        self.hypo_arm_label.set(arm_label)
        self._hypo_arm_score_lbl.configure(fg=arm_color)
        self._hypo_arm_status_lbl.configure(fg=arm_color)

        # Connection status indicator in the header
        if latest["connected"]:
            self.conn_var.set("● CONNECTED")
            self.conn_lbl.configure(fg="#90ee90")
        else:
            self.conn_var.set("● SEARCHING")
            self.conn_lbl.configure(fg="#aaccee")

        self.sample_var.set(f"samples: {sample_count:,}")

        # Schedule the next refresh in 200 ms
        self.root.after(200, self._update_ui)

    def _tick_clock(self):
        """Update the UTC clock in the header once per second."""
        self.clock_var.set(datetime.utcnow().strftime("%H:%M:%S UTC"))
        self.root.after(1000, self._tick_clock)

    # -------------------------------------------------------------------------
    # BLE STARTUP & SHUTDOWN
    # -------------------------------------------------------------------------

    def _start_ble(self):
        """Start the BLE background thread, wiring its status messages to the header label."""
        def update_status(msg):
            self.root.after(0, self.conn_var.set, f"● {msg.upper()}")

        start_ble_thread(update_status)

    def on_close(self):
        """Called when the user closes the window — stops BLE and saves the CSV."""
        latest["connected"] = False
        if csv_file:
            csv_file.close()
        self.root.destroy()


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    root = tk.Tk()
    app  = Dashboard(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
