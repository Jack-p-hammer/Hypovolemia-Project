import asyncio
import threading
import tkinter as tk
import csv
import os
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from bleak import BleakScanner, BleakClient
from datetime import datetime, timezone
from collections import deque
import numpy as np


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

# IR ADC count below which the sensor is considered off-body (matches firmware FINGER_ON)
FINGER_ON = 30000


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
FONT_SANS  = "Segoe UI"


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
        if csv_writer and csv_file and not csv_file.closed:
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
# SIGNAL-TO-NOISE RATIO
# Frequency-domain SNR: signal power is the power in the cardiac band (0.5–4 Hz),
# noise power is everything above 4 Hz.  Result is in dB — higher is cleaner.
# Uses the last 2 seconds of data (1000 samples at 500 Hz) for the FFT.
# =============================================================================

def compute_snr(signal_deque, fs=500, window=1000):
    """Return SNR in dB, or None if there is not enough data yet."""
    if len(signal_deque) < window:
        return None
    samples = np.array(list(signal_deque)[-window:], dtype=float)
    samples -= samples.mean()                          # remove DC before FFT
    fft_power = np.abs(np.fft.rfft(samples)) ** 2
    freqs     = np.fft.rfftfreq(window, d=1.0 / fs)
    signal_power = fft_power[(freqs >= 1) & (freqs <= 4.0)].sum()
    noise_power  = fft_power[freqs > 4.0].sum()
    if noise_power <= 0:
        return None
    return 10 * np.log10(signal_power / noise_power)


# =============================================================================
# DASHBOARD  —  the main UI window
# =============================================================================

class Dashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("Hypovolemia Monitor — Clinical Interface")
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

        tk.Label(header, text="  HYPOVOLEMIA MONITOR",
                 bg=BLUE, fg="white",
                 font=(FONT_SANS, 13, "bold")).pack(side="left", pady=10)
        tk.Label(header, text="Clinical Decision Support  ·  Live PPG Stream",
                 bg=BLUE, fg="#aaccee",
                 font=(FONT_SANS, 9)).pack(side="left", padx=(8, 0), pady=10)

        # Connection status label (updated by BLE thread callbacks)
        self.conn_var = tk.StringVar(value="● SEARCHING FOR DEVICE")
        self.conn_lbl = tk.Label(header, textvariable=self.conn_var,
                                 bg=BLUE, fg="#aaccee",
                                 font=(FONT_SANS, 10))
        self.conn_lbl.pack(side="right", padx=16)

        # Live UTC clock
        self.clock_var = tk.StringVar()
        tk.Label(header, textvariable=self.clock_var,
                 bg=BLUE, fg="#aaccee",
                 font=(FONT_SANS, 10)).pack(side="right", padx=8)
        self._tick_clock()

        # --- 2x2 grid of cards in the main area ---
        grid = tk.Frame(self.root, bg=BG_MAIN)
        grid.pack(fill="both", expand=True, padx=12, pady=10)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        grid.rowconfigure(0, weight=0)
        grid.rowconfigure(1, weight=1)
        grid.rowconfigure(2, weight=0)

        self._build_temp_card(grid, row=0, col=0)              # top-left:  temperatures
        self._build_feedback_card(grid, row=0, col=1)          # top-right: delta-T feedback
        self._build_ppg_card(grid, row=1, col=0, rowspan=2)    # left: PPG waveforms (spans rows 1–2)
        self._build_hypo_card(grid, row=1, col=1)              # right: hypovolemia scores
        self._build_signal_quality_card(grid, row=2, col=1)    # right: signal quality

        # --- Footer strip along the bottom ---
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x")
        footer = tk.Frame(self.root, bg=BG_CARD)
        footer.pack(fill="x")
        tk.Label(footer, text="  Device: PPG_Forearm",
                 bg=BG_CARD, fg=TEXT_MUTED,
                 font=(FONT_SANS, 9)).pack(side="left", pady=4)
        self.sample_var = tk.StringVar(value="Recorded Samples: 0")
        tk.Label(footer, textvariable=self.sample_var,
                 bg=BG_CARD, fg=TEXT_MUTED,
                 font=(FONT_SANS, 9)).pack(side="right", padx=12, pady=4)

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
                 font=(FONT_SANS, 8, "bold")).pack(anchor="w", padx=2, pady=(0, 3))

        inner = tk.Frame(outer, bg=WHITE,
                         highlightbackground=BORDER,
                         highlightthickness=1)
        inner.pack(fill="both", expand=True)
        return inner

    # -------------------------------------------------------------------------
    # CARD: TEMPERATURE
    # -------------------------------------------------------------------------

    def _draw_temp_toggle(self):
        """Redraw the C/F pill toggle to match self.temp_unit."""
        c  = self._toggle_canvas
        W  = 72
        H  = 26
        r  = H // 2
        pad = 3
        bg  = BLUE if self.temp_unit == "F" else BORDER

        c.delete("all")

        # Pill background — two circles + filled rectangle between them
        c.create_oval(0, 0, H, H, fill=bg, outline=bg)
        c.create_oval(W - H, 0, W, H, fill=bg, outline=bg)
        c.create_rectangle(r, 0, W - r, H, fill=bg, outline=bg)

        # Sliding thumb
        cx = (W - r) if self.temp_unit == "F" else r
        c.create_oval(cx - r + pad, pad, cx + r - pad, H - pad,
                      fill=WHITE, outline=WHITE)

        # C / F labels
        c.create_text(r, H // 2, text="C",
                      fill=TEXT_MUTED if self.temp_unit == "F" else bg,
                      font=(FONT_MONO, 9, "bold"))
        c.create_text(W - r, H // 2, text="F",
                      fill=TEXT_MUTED if self.temp_unit == "C" else bg,
                      font=(FONT_MONO, 9, "bold"))

    def _toggle_temp_unit(self):
        """Switch the temperature display between °C and °F and redraw the toggle."""
        self.temp_unit = "F" if self.temp_unit == "C" else "C"
        self.unit_var.set("°F" if self.temp_unit == "F" else "°C")
        self._draw_temp_toggle()

    def _build_temp_card(self, parent, row, col):
        """Two rows showing live neck and arm temperatures with a C/F pill toggle."""
        card = self._make_card(parent, "PATIENT TEMPERATURE", row, col)

        self.temp_unit = "C"
        self.unit_var  = tk.StringVar(value="°C")

        self.t_neck_var = tk.StringVar(value="--.-")
        self.t_arm_var  = tk.StringVar(value="--.-")

        # --- Toggle switch row ---
        toggle_row = tk.Frame(card, bg=WHITE)
        toggle_row.pack(anchor="e", padx=16, pady=(8, 0))

        self._toggle_canvas = tk.Canvas(toggle_row, width=72, height=26,
                                        bg=WHITE, highlightthickness=0,
                                        cursor="hand2")
        self._toggle_canvas.pack(side="right")
        self._toggle_canvas.bind("<Button-1>", lambda e: self._toggle_temp_unit())
        self._draw_temp_toggle()

        # --- Sensor value rows ---
        sensors = [
            ("Neck Site",    self.t_neck_var),
            ("Forearm Site", self.t_arm_var),
        ]

        for label_text, value_var in sensors:
            row_frame = tk.Frame(card, bg=WHITE,
                                 highlightbackground=BORDER,
                                 highlightthickness=1)
            row_frame.pack(fill="x", padx=16, pady=8)

            tk.Label(row_frame, text=label_text,
                     bg=WHITE, fg=TEXT_MUTED,
                     font=(FONT_SANS, 10)).pack(side="left", padx=12, pady=10)

            tk.Label(row_frame, textvariable=value_var,
                     bg=WHITE, fg=TEXT,
                     font=(FONT_MONO, 22, "bold")).pack(side="left", padx=4)

            tk.Label(row_frame, textvariable=self.unit_var,
                     bg=WHITE, fg=TEXT_MUTED,
                     font=(FONT_SANS, 11)).pack(side="left")

    # -------------------------------------------------------------------------
    # CARD: DELTA-T FEEDBACK
    # -------------------------------------------------------------------------

    def _build_feedback_card(self, parent, row, col):
        """
        Shows the temperature difference between neck and arm (dT = T_neck - T_arm),
        plus a trend indicator (Rising / Stable / Falling) based on recent neck temperature.
        """
        card = self._make_card(parent, "TEMPERATURE DIFFERENTIAL", row, col)

        tk.Label(card, text="CENTRAL–PERIPHERAL GRADIENT",
                 bg=WHITE, fg=TEXT_MUTED,
                 font=(FONT_SANS, 9)).pack(anchor="w", padx=16, pady=(14, 2))

        self.delta_var = tk.StringVar(value="ΔT:  —")
        tk.Label(card, textvariable=self.delta_var,
                 bg=WHITE, fg=AMBER,
                 font=(FONT_MONO, 18, "bold")).pack(anchor="w", padx=16, pady=2)

        self.trend_var = tk.StringVar(value="ΔT Stage:  —")
        self.trend_lbl = tk.Label(card, textvariable=self.trend_var,
                                  bg=WHITE, fg=AMBER,
                                  font=(FONT_SANS, 13))
        self.trend_lbl.pack(anchor="w", padx=16, pady=(0, 12))

    # -------------------------------------------------------------------------
    # CARD: PPG WAVEFORMS
    # -------------------------------------------------------------------------

    def _build_ppg_card(self, parent, row, col, rowspan=1):
        """
        Live scrolling graph of the raw neck and arm PPG signals.
        The graph shows the last GRAPH_WINDOW_SECONDS seconds of data.
        """
        card = self._make_card(parent, "PHOTOPLETHYSMOGRAPHY (PPG)", row, col, rowspan=rowspan)

        # Create a figure with two vertically stacked subplots sharing the x-axis
        self.fig_ppg, (self.ax_neck, self.ax_arm) = plt.subplots(
            2, 1, figsize=(5, 3.2), sharex=True
        )
        self.fig_ppg.patch.set_facecolor(WHITE)
        self.fig_ppg.subplots_adjust(hspace=0.1, left=0.08, right=0.97, top=0.93, bottom=0.12)

        # Style both axes the same way
        for ax, label in [(self.ax_neck, "Neck"), (self.ax_arm, "Forearm")]:
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

        # Off-body warning overlays — shown when IR is below FINGER_ON threshold
        _warn_style = dict(
            ha="center", va="center",
            fontsize=13, fontfamily="monospace",
            color="white", fontweight="bold",
            bbox=dict(boxstyle="round,pad=1.2", facecolor="#c0392b",
                      alpha=0.95, edgecolor="#7b241c", linewidth=2.5),
            visible=False,
            zorder=10,
        )
        self._neck_warning = self.ax_neck.text(
            0.5, 0.5, "Sensor not detecting body\nPlease place on patient",
            transform=self.ax_neck.transAxes, **_warn_style
        )
        self._arm_warning = self.ax_arm.text(
            0.5, 0.5, "Sensor not detecting body\nPlease place on patient",
            transform=self.ax_arm.transAxes, **_warn_style
        )

        # SNR readouts — top-right corner of each subplot, updated each frame
        self._snr_neck_text = self.ax_neck.text(
            0.99, 0.97, "SNR —", transform=self.ax_neck.transAxes,
            ha="right", va="top", fontsize=7, color=TEXT_MUTED,
            fontfamily="monospace", zorder=9
        )
        self._snr_arm_text = self.ax_arm.text(
            0.99, 0.97, "SNR —", transform=self.ax_arm.transAxes,
            ha="right", va="top", fontsize=7, color=TEXT_MUTED,
            fontfamily="monospace", zorder=9
        )

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
        card = self._make_card(parent, "PERFUSION INDEX", row, col)

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
            (0, "NECK SITE",    self.hypo_neck_score, self.hypo_neck_label),
            (1, "FOREARM SITE", self.hypo_arm_score,  self.hypo_arm_label),
        ]

        for column_index, title, score_var, label_var in panels:
            box = tk.Frame(container, bg=BG_CARD,
                           highlightbackground=BORDER, highlightthickness=1)
            box.grid(row=0, column=column_index, padx=6, pady=4, sticky="nsew")

            tk.Label(box, text=title,
                     bg=BG_CARD, fg=TEXT_MUTED,
                     font=(FONT_SANS, 9, "bold")).pack(pady=(10, 4))

            score_lbl = tk.Label(box, textvariable=score_var,
                                 bg=BG_CARD, fg=GREEN,
                                 font=(FONT_MONO, 32, "bold"))
            score_lbl.pack()

            status_lbl = tk.Label(box, textvariable=label_var,
                                  bg=BG_CARD, fg=TEXT_MUTED,
                                  font=(FONT_SANS, 9))
            status_lbl.pack(pady=(0, 10))

            # Save references so _update_ui() can change the text color
            if column_index == 0:
                self._hypo_neck_score_lbl  = score_lbl
                self._hypo_neck_status_lbl = status_lbl
            else:
                self._hypo_arm_score_lbl   = score_lbl
                self._hypo_arm_status_lbl  = status_lbl

    # -------------------------------------------------------------------------
    # CARD: SIGNAL QUALITY
    # -------------------------------------------------------------------------

    def _build_signal_quality_card(self, parent, row, col):
        """
        Two rows showing SNR-based signal quality for neck and arm.
        GOOD ≥ 10 dB / FAIR 5–10 dB / POOR < 5 dB
        """
        card = self._make_card(parent, "SIGNAL QUALITY", row, col)

        self._sq_rows = {}
        for site in ("NECK SITE", "FOREARM SITE"):
            row_frame = tk.Frame(card, bg=WHITE,
                                 highlightbackground=BORDER, highlightthickness=1)
            row_frame.pack(fill="x", padx=16, pady=6)

            tk.Label(row_frame, text=site,
                     bg=WHITE, fg=TEXT_MUTED,
                     font=(FONT_SANS, 10)).pack(side="left", padx=12, pady=8)

            snr_var    = tk.StringVar(value="— dB")
            status_var = tk.StringVar(value="WAITING")

            tk.Label(row_frame, textvariable=snr_var,
                     bg=WHITE, fg=TEXT,
                     font=(FONT_MONO, 12, "bold")).pack(side="left", padx=(0, 10))

            status_lbl = tk.Label(row_frame, textvariable=status_var,
                                  bg=WHITE, fg=TEXT_MUTED,
                                  font=(FONT_SANS, 10, "bold"))
            status_lbl.pack(side="right", padx=12)

            self._sq_rows[site] = (snr_var, status_var, status_lbl)

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

        # Compute SNR for label update below
        neck_snr = compute_snr(neck_data)
        arm_snr  = compute_snr(arm_data)

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

        # Show off-body warning on arm subplot when IR is below threshold and connected
        arm_off_body = latest["connected"] and latest["arm"] < FINGER_ON
        # Neck: only warn if we have real neck data (non-zero) that's below threshold
        neck_off_body = 0 < latest["neck"] < FINGER_ON
        self._arm_warning.set_visible(arm_off_body)
        self._neck_warning.set_visible(neck_off_body)

        # Fade lines when off-body so the warning text draws the eye
        self.line_arm.set_alpha(0.12 if arm_off_body else 1.0)
        self.line_arm.set_color("#aaaaaa" if arm_off_body else RED)
        self.line_neck.set_alpha(0.12 if neck_off_body else 1.0)
        self.line_neck.set_color("#aaaaaa" if neck_off_body else BLUE)

        # Update SNR readouts (neck_snr / arm_snr computed above)
        self._snr_neck_text.set_text(f"SNR {neck_snr:.1f} dB" if neck_snr is not None else "SNR —")
        self._snr_arm_text.set_text( f"SNR {arm_snr:.1f} dB"  if arm_snr  is not None else "SNR —")

    def _update_ui(self):
        """
        Called every 200 ms to refresh all the text widgets on the dashboard.
        Covers temperatures, delta-T, trend, hypovolemia scores, and connection state.
        """
        t_neck = latest["t_neck"]   # always °C from sensor
        t_arm  = latest["t_arm"]    # always °C from sensor

        # Convert for display only — delta-T thresholds stay in °C
        if self.temp_unit == "F":
            self.t_neck_var.set(f"{t_neck * 9/5 + 32:.1f}")
            self.t_arm_var.set(f"{t_arm  * 9/5 + 32:.1f}")
        else:
            self.t_neck_var.set(f"{t_neck:.1f}")
            self.t_arm_var.set(f"{t_arm:.1f}")

        # Delta-T: difference between neck and arm temperature
        scale = 1.5 # scaling factor to make the delta-T more visually prominent; adjust as needed
        delta = scale * (t_neck - t_arm)
        self.delta_var.set(f"ΔT:  {delta/scale:.1f}")

        # Trend: compare the average of the last 10 neck samples to samples from
        # 40-50 readings ago.  A positive diff means the temperature is rising.
        if len(t_neck_data) < 50:
            match delta:
                case _ if delta < 3.0:
                    trend_text, trend_color = "Normal", TEXT_MUTED
                case _ if delta > 3.0 and delta < 5.0:
                    trend_text, trend_color = "Early",  BLUE
                case _ if delta >= 5.0 and delta < 7.0:
                    trend_text, trend_color = "Mid",  AMBER
                case _ if delta >= 7.0 and delta < 10.0:
                    trend_text, trend_color = "Moderate",  RED
                case _ if delta >= 10.0:
                    trend_text, trend_color = "Severe",  RED         
        else:
            trend_text, trend_color = "→  Awaiting data...", TEXT_MUTED

        self.trend_var.set(f"ΔT Stage:  {trend_text}")
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

        # Signal quality card
        def snr_to_quality(snr):
            if snr is None:               return "— dB",          "WAITING", TEXT_MUTED
            if snr >= 10:                 return f"{snr:.1f} dB",  "GOOD",    GREEN
            if snr >= 5:                  return f"{snr:.1f} dB",  "FAIR",    AMBER
            return                               f"{snr:.1f} dB",  "POOR",    RED

        neck_off_body = 0 < latest["neck"] < FINGER_ON
        arm_off_body  = latest["connected"] and latest["arm"] < FINGER_ON

        for site, deque_, off_body in (
            ("NECK SITE",    neck_data, neck_off_body),
            ("FOREARM SITE", arm_data,  arm_off_body),
        ):
            if off_body:
                snr_val, status_text, status_color = "—", "OFF BODY", TEXT_MUTED
            else:
                snr_val, status_text, status_color = snr_to_quality(compute_snr(deque_))
            snr_var, status_var, status_lbl = self._sq_rows[site]
            snr_var.set(snr_val)
            status_var.set(status_text)
            status_lbl.configure(fg=status_color)

        # Connection status indicator in the header
        if latest["connected"]:
            self.conn_var.set("● DEVICE CONNECTED")
            self.conn_lbl.configure(fg="#90ee90")
        else:
            self.conn_var.set("● SEARCHING FOR DEVICE")
            self.conn_lbl.configure(fg="#aaccee")

        self.sample_var.set(f"Recorded Samples: {sample_count:,}")

        # Schedule the next refresh in 200 ms
        self.root.after(200, self._update_ui)

    def _tick_clock(self):
        """Update the UTC clock in the header once per second."""
        self.clock_var.set(datetime.now(timezone.utc).strftime("%H:%M:%S UTC"))
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
