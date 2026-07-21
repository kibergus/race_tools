# Fanatec Wheel Strength Training

An app to build up muscles used for steering without being too bored. The trick is: for muscle to grow the force needs to be strong, but without abrupt peaks, which otherwise may damage them. A strong direct drive wheel is great for that.

This app works only for Fanatec wheels, under Linux and probably only under Wayland. But it was entirely coded with Antigravity. This kind of small localized task is simple enough for veryfing through the result, rather then by reading the code. I wouldn't recommend this approach for larger projects though. But it means that if you need it on Windows or for another wheel brand, Antigravity will likely manage to do that. 

## Features
- **Static Centering Force**: Applies a configurable peak resistance to pull the wheel back to the center.
- **Gradual Central Notch**: The resistance scales linearly between -15° and +15° of wheel deflection and saturates to maximum force past 15° to simulate a heavy spring.
- **Sleek HUD Visualization**: A fullscreen 180° dashboard showing target position, current wheel position, alignment error, and real-time resistance strength.
- **External Application Mode**: Track and shift third-party windows (such as `mpv`) horizontally based on target deflection, requiring you to steer against the force to keep the window centered on screen.
- **Dynamic Target Trajectory**:
  - Every **2 seconds**, the target smoothly shifts by 5° in a random direction (using cosine interpolation).
  - Every **15 seconds**, the target jumps to a random position between -180° and 180° and interpolates over a 5-second interval (disabling the 2-second small alterations during this phase).
- **Dynamic Audio Feedback cue / punusment**: Plays Lyria generated "Hold the wheel!" hit song. If target deflection is ≤ 2.0°, volume is 0%; volume scales linearly up to 100% as deflection reaches 10.0°.

- **Demo Mode**: Automatically detects if a Fanatec wheel is not plugged in and switches to a mouse-emulated steering mode so the UI and trajectory logic can be easily tested without hardware.

## How to Run

Ensure your virtual environment or system Python has `evdev`, `screeninfo`, `tkinter`, `pygame`, and `python-xlib` / `xdotool` installed, then execute:

```bash
# Standard dashboard HUD GUI
python3 strength_training/main.py

# External Application Tracking Mode (e.g. mpv video player)
python3 strength_training/main.py --app="mpv my.mp4"
```

### External Application Mode (`--app`)
When launched with `--app='<command>'` (such as `mpv my.mp4`), the script launches the application, tracks its window, matches the window size to the screen, and dynamically shifts the window left and right based on the steering target trajectory.
- At 0° net deflection (wheel matches target angle), the window remains centered on screen.
- At ±90° deflection, the window shifts horizontally so that 1/4 of the window remains visible on the screen.
- The steering wheel counteracts the deflection: turning the wheel centers the window!
- Close the external app window or press `Ctrl+C` in terminal to exit.

### Controls
- **Mouse Movement** (Demo Mode only): Moves the emulated wheel position.
- **[ / ]**: Decrease / increase FFB resistance level in steps of 10%.
- **V**: Cycle visual themes (Cat Arrow, Regular, Full Cat).
- **M**: Mute / Unmute dynamic audio feedback cue (persisted across sessions in config).
- **R**: Reset session alignment error and average deviation statistics.
- **S**: Switch screen in a multiscreen configuration.
- **Escape** or **Q**: Safe exit (automatically zeroes all forces on the wheel and destroys the window).

## Configuration

You can customize the FFB parameters at the top of `strength_training/main.py`:

```python
MAX_FORCE = 32767       # Peak force applied to the wheel (max 32767)
FORCE_DIRECTION = 1     # Set to -1 if the wheel pushes away from center instead of pulling
STIFFNESS_ANGLE = 15.0  # Angle deflection (degrees) where force saturates to MAX_FORCE
DAMPING_COEF = 40       # Damping coefficient to prevent high-frequency oscillations
LOOP_FREQUENCY = 200    # Control loop update rate in Hz (recommended 200Hz+)
```
