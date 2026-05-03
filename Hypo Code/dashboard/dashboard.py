"""
dashboard.py — PPG Hypovolemia Monitor

Connects to the forearm BLE node, parses batched PPG + temperature packets,
displays live waveforms, and logs all data to a timestamped CSV file.

Expected BLE packet format (ASCII, semicolon-delimited batches):
    "IR_neck,RED_neck,T_neck,IR_arm,RED_arm,T_arm;..."

Hypovolemia indicator: ΔT = T_neck - T_arm
    Sustained ΔT > DELTA_T_THRESHOLD °C suggests peripheral vasoconstriction.

Usage:
    pip install -r requirements.txt
    python dashboard.py
"""

import asyncio
import csv
import threading
import time
from collections import deque
from datetime import datetime

import matplotlib
try:
    matplotlib.use("TkAgg")
except Exception:
    pass  # fall back to whatever backend is available
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import numpy as np
from bleak import BleakClient, BleakScanner

# ---- BLE config ----
DEVICE_NAME = "PPG_Forearm"
CHAR_UUID   = "abcdefab-1234-1234-1234-abcdefabcdef"

# ---- Display config ----
WINDOW_SAMPLES    = 500          # 1 second at 500 Hz
IR_RED_YLIM       = (50000, 150000)
TEMP_YLIM         = (34.0, 40.0)
DELTA_T_YLIM      = (-1.0, 6.0)
DELTA_T_THRESHOLD = 2.0          # °C — warn above this
PLOT_INTERVAL_MS  = 50           # matplotlib refresh rate (~20 fps)

# ---- Ring buffers (thread-safe for CPython deque append) ----
ir_neck  = deque([0.0] * WINDOW_SAMPLES, maxlen=WINDOW_SAMPLES)
red_neck = deque([0.0] * WINDOW_SAMPLES, maxlen=WINDOW_SAMPLES)
t_neck   = deque([0.0] * WINDOW_SAMPLES, maxlen=WINDOW_SAMPLES)
ir_arm   = deque([0.0] * WINDOW_SAMPLES, maxlen=WINDOW_SAMPLES)
red_arm  = deque([0.0] * WINDOW_SAMPLES, maxlen=WINDOW_SAMPLES)
t_arm    = deque([0.0] * WINDOW_SAMPLES, maxlen=WINDOW_SAMPLES)

# ---- CSV logging ----
csv_filename = f"ppg_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
csv_file     = open(csv_filename, "w", newline="")
csv_writer   = csv.writer(csv_file)
csv_writer.writerow(["host_timestamp", "ir_neck", "red_neck", "t_neck",
                     "ir_arm", "red_arm", "t_arm"])

# ---- Packet counter for status bar ----
packet_count = 0
sample_count = 0


# ------------------------------------------------------------------
# Packet parsing
# ------------------------------------------------------------------
def parse_batch(raw: bytes) -> list:
    """
    Parse a BLE notification payload into a list of
    [ir_neck, red_neck, t_neck, ir_arm, red_arm, t_arm] float rows.
    """
    text    = raw.decode("utf-8", errors="ignore").strip()
    samples = []
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(",")
        if len(parts) == 6:
            try:
                samples.append([float(p) for p in parts])
            except ValueError:
                pass
    return samples


# ------------------------------------------------------------------
# BLE notification callback
# ------------------------------------------------------------------
def notification_handler(sender, data: bytearray):
    global packet_count, sample_count
    ts      = time.time()
    samples = parse_batch(bytes(data))
    packet_count += 1
    for s in samples:
        ir_neck.append(s[0])
        red_neck.append(s[1])
        t_neck.append(s[2])
        ir_arm.append(s[3])
        red_arm.append(s[4])
        t_arm.append(s[5])
        csv_writer.writerow([f"{ts:.6f}", s[0], s[1], s[2], s[3], s[4], s[5]])
        sample_count += 1
    csv_file.flush()


# ------------------------------------------------------------------
# BLE async task (runs in a background thread)
# Retries indefinitely until a connection is established and maintained.
# ------------------------------------------------------------------
async def ble_task():
    while True:
        # ---- Scan ----
        # Use discover() instead of find_device_by_name() because on Windows
        # the ESP32 puts its name in the scan response, which find_device_by_name
        # can miss. discover() collects both advertisement and scan response.
        print(f"Scanning for '{DEVICE_NAME}'...")
        try:
            found = await BleakScanner.discover(timeout=5.0, return_adv=True)
            device = next(
                (d for d, adv in found.values()
                 if (d.name or adv.local_name or "") == DEVICE_NAME),
                None
            )
        except Exception as e:
            print(f"Scan error: {e} — retrying in 3s")
            await asyncio.sleep(3.0)
            continue

        if device is None:
            print(f"  '{DEVICE_NAME}' not found — retrying in 3s")
            await asyncio.sleep(3.0)
            continue

        print(f"Found {DEVICE_NAME} at {device.address}, connecting...")

        # ---- Connect ----
        try:
            async with BleakClient(device, timeout=10.0) as client:
                print("Connected.")

                # MTU negotiation — skip silently if unsupported (e.g. Windows)
                try:
                    await client.request_mtu(512)
                    print(f"MTU: {client.mtu_size} bytes")
                except Exception:
                    pass

                await client.start_notify(CHAR_UUID, notification_handler)
                print(f"Subscribed to notifications. Logging to {csv_filename}")

                # Stay connected until the device drops
                while client.is_connected:
                    await asyncio.sleep(0.5)

                print("Device disconnected — rescanning...")

        except Exception as e:
            print(f"Connection error: {e} — retrying in 3s")
            await asyncio.sleep(3.0)


def run_ble_thread():
    # Explicitly create a new event loop for this thread.
    # asyncio.run() can be unreliable inside non-main threads on Windows.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ble_task())


# ------------------------------------------------------------------
# Matplotlib dashboard
# ------------------------------------------------------------------
fig = plt.figure(figsize=(15, 9))
fig.suptitle("PPG Hypovolemia Monitor", fontsize=14, fontweight="bold")

gs      = fig.add_gridspec(3, 2, hspace=0.45, wspace=0.35)
ax_ir_n = fig.add_subplot(gs[0, 0])
ax_ir_a = fig.add_subplot(gs[0, 1])
ax_re_n = fig.add_subplot(gs[1, 0])
ax_re_a = fig.add_subplot(gs[1, 1])
ax_temp = fig.add_subplot(gs[2, 0])
ax_delt = fig.add_subplot(gs[2, 1])

x = np.arange(WINDOW_SAMPLES)

for ax, title, color in [
    (ax_ir_n, "IR — Neck",  "steelblue"),
    (ax_ir_a, "IR — Arm",   "dodgerblue"),
    (ax_re_n, "RED — Neck", "firebrick"),
    (ax_re_a, "RED — Arm",  "tomato"),
]:
    ax.set_title(title, fontsize=10)
    ax.set_ylim(*IR_RED_YLIM)
    ax.set_xlim(0, WINDOW_SAMPLES - 1)
    ax.set_ylabel("ADC counts")
    ax.tick_params(labelbottom=False)

ax_temp.set_title("Skin Temperature (°C)", fontsize=10)
ax_temp.set_ylim(*TEMP_YLIM)
ax_temp.set_xlim(0, WINDOW_SAMPLES - 1)
ax_temp.set_ylabel("°C")
ax_temp.tick_params(labelbottom=False)

ax_delt.set_title(f"ΔT Neck − Arm  (warning > {DELTA_T_THRESHOLD} °C)", fontsize=10)
ax_delt.set_ylim(*DELTA_T_YLIM)
ax_delt.set_xlim(0, WINDOW_SAMPLES - 1)
ax_delt.set_ylabel("°C")
ax_delt.axhline(y=DELTA_T_THRESHOLD, color="red", linestyle="--", linewidth=1.0, label=f"{DELTA_T_THRESHOLD} °C")
ax_delt.tick_params(labelbottom=False)

# Initial line objects
ln_ir_n,  = ax_ir_n.plot(x, list(ir_neck),  color="steelblue", lw=0.8)
ln_ir_a,  = ax_ir_a.plot(x, list(ir_arm),   color="dodgerblue", lw=0.8)
ln_re_n,  = ax_re_n.plot(x, list(red_neck), color="firebrick", lw=0.8)
ln_re_a,  = ax_re_a.plot(x, list(red_arm),  color="tomato", lw=0.8)
ln_tn,    = ax_temp.plot(x, list(t_neck),   color="steelblue", lw=1.0, label="Neck")
ln_ta,    = ax_temp.plot(x, list(t_arm),    color="darkorange", lw=1.0, label="Arm")
ax_temp.legend(fontsize=8, loc="upper right")
ax_delt.legend(fontsize=8, loc="upper right")

delta_init = [n - a for n, a in zip(t_neck, t_arm)]
ln_dt,    = ax_delt.plot(x, delta_init,  color="purple", lw=1.0)

status_txt = ax_delt.text(
    0.02, 0.88, "", transform=ax_delt.transAxes,
    fontsize=9, fontweight="bold", va="top"
)
info_txt = fig.text(
    0.5, 0.01, "", ha="center", fontsize=8, color="gray"
)


def update(frame):
    y_ir_n  = list(ir_neck)
    y_ir_a  = list(ir_arm)
    y_re_n  = list(red_neck)
    y_re_a  = list(red_arm)
    y_tn    = list(t_neck)
    y_ta    = list(t_arm)

    ln_ir_n.set_ydata(y_ir_n)
    ln_ir_a.set_ydata(y_ir_a)
    ln_re_n.set_ydata(y_re_n)
    ln_re_a.set_ydata(y_re_a)
    ln_tn.set_ydata(y_tn)
    ln_ta.set_ydata(y_ta)

    delta = [n - a for n, a in zip(y_tn, y_ta)]
    ln_dt.set_ydata(delta)

    # Hypovolemia indicator: 2-second rolling average of ΔT
    recent_n = max(1, min(1000, WINDOW_SAMPLES))
    avg_delta = float(np.mean(delta[-recent_n:]))

    if avg_delta > DELTA_T_THRESHOLD:
        status_txt.set_text(f"WARNING  ΔT = {avg_delta:.2f} °C")
        status_txt.set_color("red")
    else:
        status_txt.set_text(f"Normal   ΔT = {avg_delta:.2f} °C")
        status_txt.set_color("green")

    info_txt.set_text(
        f"Packets received: {packet_count}   Samples logged: {sample_count}   "
        f"File: {csv_filename}"
    )

    return (ln_ir_n, ln_ir_a, ln_re_n, ln_re_a, ln_tn, ln_ta, ln_dt,
            status_txt, info_txt)


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------
if __name__ == "__main__":
    # Start BLE in background thread
    ble_thread = threading.Thread(target=run_ble_thread, daemon=True)
    ble_thread.start()

    # Run matplotlib in main thread (required on most platforms)
    ani = animation.FuncAnimation(
        fig, update, interval=PLOT_INTERVAL_MS, blit=True, cache_frame_data=False
    )
    plt.show()

    # Cleanup on window close
    csv_file.close()
    print(f"Session saved to {csv_filename}")
