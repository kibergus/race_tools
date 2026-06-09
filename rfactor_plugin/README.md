# BrBrDb Telemetry for rFactor 2 / KartSim

A native, high-performance C++ Plugin for **rFactor 2** and **KartSim** designed to log and stream high-fidelity physical telemetry channels. I wrote it because DAMPlugin can only write to files and I had to exit the simulator and run a separate application import the data. SecondMonitor is nice, but I never managed to get all the data I needed like slip angles.

The plugin writes the telemetry to CSV files (because CSV is easy to parse) and modern computers can deal with some inefficiency that comes with the format. And it also can stream the same file over HTTP in real time. HTTP / TCP here is a concious choice: it makes things much simpler than UDP and for my purposes strict real time is not needed. And if I needed real time I'd probably run the server on the same machine and the overhead would be negligible. 

The plugin comes with an installer/configurator utility. Run the installer to add/remove the plugin or reconfigure the plugin.

Server side of the project will also be open-sourced but is not ready yet.

## 🚀 Key Features

* **Zero-Overhead Hooking:** Leverages the native C++ rFactor 2 Internals SDK callback structure to tap directly into the engine's physics tick space.
* **Micro-Stutter Prevention:** Operates an asynchronous background thread-safe logger queue. Physics callbacks are queued and processed asynchronously, ensuring disk I/O operations never delay the game's physics thread (which runs at 90Hz-100Hz).
* **Individual Wheel Granularity:** Logs telemetry for all four wheels independently.

## ⚙️ Compilation & Installation

This plugin is designed to be compiled for Windows x64. Ensure you have **Visual Studio** (with the *Desktop Development with C++* workload) installed.

A portable PowerShell script `build.ps1` is provided to fully automate the build and installation process:
