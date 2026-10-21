// Copyright 2026 Alexey Guseynov. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
// ==============================================================================

#include <windows.h>
#include <shellapi.h>
#include <iostream>
#include <string>
#include <filesystem>
#include <atomic>
#include <condition_variable>
#include <mutex>

#include "ace_shared_memory.h"
#include "telemetry_collector.h"
#include "config.h"
#include "logger.h"
#include "net_utils.h"

using namespace race_tools;

namespace {

std::atomic<bool> g_exitRequested{false};
std::condition_variable g_exitCv;
std::mutex g_exitMutex;

std::atomic<bool> g_cleanedUp{false};
std::condition_variable g_cleanupCv;
std::mutex g_cleanupMutex;

HWND g_hWnd = NULL;
NOTIFYICONDATAW g_nid = {0};
std::wstring g_moduleDir;
std::wstring g_logFilePath;
std::wstring g_telemetryDirPath;
TelemetryCollector* g_pCollector = nullptr;
UINT g_wmTaskbarCreated = 0;

void OpenInstaller() {
  HWND hWndExisting = FindWindowW(NULL, L"BrBrDb Telemetry");
  if (hWndExisting) {
    ShowWindow(hWndExisting, SW_RESTORE);
    SetForegroundWindow(hWndExisting);
    return;
  }

  std::wstring installerExe = g_moduleDir + L"\\BrBrDbTelemetryInstaller.exe";
  if (std::filesystem::exists(installerExe)) {
    ShellExecuteW(NULL, L"open", installerExe.c_str(), NULL, g_moduleDir.c_str(), SW_SHOWNORMAL);
    return;
  }

  // Fallbacks for development builds
  std::vector<std::wstring> devPaths = {
    g_moduleDir + L"\\..\\..\\..\\installer\\publish\\BrBrDbTelemetryInstaller.exe",
    g_moduleDir + L"\\..\\..\\..\\installer\\bin\\Release\\net8.0-windows\\win-x64\\BrBrDbTelemetryInstaller.exe",
    g_moduleDir + L"\\..\\..\\..\\installer\\bin\\Debug\\net8.0-windows\\win-x64\\BrBrDbTelemetryInstaller.exe"
  };
  for (const auto& devPath : devPaths) {
    if (std::filesystem::exists(devPath)) {
      ShellExecuteW(NULL, L"open", devPath.c_str(), NULL, NULL, SW_SHOWNORMAL);
      return;
    }
  }

  if (std::filesystem::exists(g_telemetryDirPath)) {
    ShellExecuteW(NULL, L"open", g_telemetryDirPath.c_str(), NULL, NULL, SW_SHOW);
  }
}

BOOL WINAPI ConsoleCtrlHandler(DWORD ctrlType) {
  g_exitRequested = true;
  if (g_hWnd) {
    PostMessageW(g_hWnd, WM_CLOSE, 0, 0);
  }
  {
    std::lock_guard<std::mutex> lock(g_exitMutex);
    g_exitCv.notify_all();
  }

  if (ctrlType == CTRL_CLOSE_EVENT || ctrlType == CTRL_SHUTDOWN_EVENT) {
    std::unique_lock<std::mutex> lock(g_cleanupMutex);
    g_cleanupCv.wait_for(lock, std::chrono::seconds(4), []() { return g_cleanedUp.load(); });
    return TRUE;
  }
  return TRUE;
}

constexpr UINT WM_TRAYICON = WM_USER + 1;
constexpr UINT ID_TRAY_STATUS = 1001;
constexpr UINT ID_TRAY_OPEN_INSTALLER = 1002;
constexpr UINT ID_TRAY_OPEN_TELEMETRY = 1003;
constexpr UINT ID_TRAY_OPEN_LOGS = 1004;
constexpr UINT ID_TRAY_EXIT = 1005;

LRESULT CALLBACK WndProc(HWND hWnd, UINT msg, WPARAM wParam, LPARAM lParam) {
  if (g_wmTaskbarCreated != 0 && msg == g_wmTaskbarCreated) {
    Shell_NotifyIconW(NIM_ADD, &g_nid);
    return 0;
  }

  if (msg == WM_TRAYICON) {
    if (lParam == WM_LBUTTONUP || lParam == WM_LBUTTONDBLCLK) {
      OpenInstaller();
      return 0;
    }

    if (lParam == WM_RBUTTONUP) {
      POINT pt;
      GetCursorPos(&pt);
      HMENU hMenu = CreatePopupMenu();

      std::wstring statusStr = L"BrBrDb: Idle";
      if (g_pCollector) {
        std::string simName = g_pCollector->GetConnectedSimulatorName();
        if (g_pCollector->IsSessionActive()) {
          statusStr = L"Recording: " + Utf8ToWide(simName);
        } else {
          statusStr = L"Ready: " + Utf8ToWide(simName);
        }
      }

      InsertMenuW(hMenu, 0, MF_BYPOSITION | MF_STRING | MF_GRAYED, ID_TRAY_STATUS, statusStr.c_str());
      InsertMenuW(hMenu, 1, MF_BYPOSITION | MF_SEPARATOR, 0, NULL);
      InsertMenuW(hMenu, 2, MF_BYPOSITION | MF_STRING, ID_TRAY_OPEN_INSTALLER, L"Open BrBrDb Telemetry");
      InsertMenuW(hMenu, 3, MF_BYPOSITION | MF_SEPARATOR, 0, NULL);
      InsertMenuW(hMenu, 4, MF_BYPOSITION | MF_STRING, ID_TRAY_OPEN_TELEMETRY, L"Open Telemetry Folder");
      InsertMenuW(hMenu, 5, MF_BYPOSITION | MF_STRING, ID_TRAY_OPEN_LOGS, L"Open Log File");
      InsertMenuW(hMenu, 6, MF_BYPOSITION | MF_SEPARATOR, 0, NULL);
      InsertMenuW(hMenu, 7, MF_BYPOSITION | MF_STRING, ID_TRAY_EXIT, L"Exit");

      SetMenuDefaultItem(hMenu, ID_TRAY_OPEN_INSTALLER, FALSE);

      SetForegroundWindow(hWnd);
      TrackPopupMenu(hMenu, TPM_RIGHTBUTTON, pt.x, pt.y, 0, hWnd, NULL);
      PostMessageW(hWnd, WM_NULL, 0, 0);
      DestroyMenu(hMenu);
    }
    return 0;
  } else if (msg == WM_COMMAND) {
    int id = LOWORD(wParam);
    if (id == ID_TRAY_OPEN_INSTALLER) {
      OpenInstaller();
    } else if (id == ID_TRAY_OPEN_TELEMETRY) {
      ShellExecuteW(NULL, L"open", g_telemetryDirPath.c_str(), NULL, NULL, SW_SHOW);
    } else if (id == ID_TRAY_OPEN_LOGS) {
      ShellExecuteW(NULL, L"open", g_logFilePath.c_str(), NULL, NULL, SW_SHOW);
    } else if (id == ID_TRAY_EXIT) {
      g_exitRequested = true;
      PostQuitMessage(0);
    }
    return 0;
  } else if (msg == WM_QUERYENDSESSION || msg == WM_ENDSESSION) {
    g_exitRequested = true;
    PostQuitMessage(0);
    return TRUE;
  } else if (msg == WM_CLOSE || msg == WM_DESTROY) {
    g_exitRequested = true;
    PostQuitMessage(0);
    return 0;
  }
  return DefWindowProcW(hWnd, msg, wParam, lParam);
}

void ShowHelp() {
  if (AttachConsole(ATTACH_PARENT_PROCESS) || GetConsoleWindow()) {
    FILE* fp;
    freopen_s(&fp, "CONOUT$", "w", stdout);
    freopen_s(&fp, "CONOUT$", "w", stderr);
  }
  std::cout << "BrBrDb Telemetry Companion Daemon (brbrdaemon)\n"
            << "Usage: brbrdaemon.exe [options]\n"
            << "Options:\n"
            << "  --tray             Run minimized in system tray (default)\n"
            << "  --headless         Run as silent background process without tray icon\n"
            << "  --console, -c      Show diagnostic console window\n"
            << "  --stop             Signal any running brbrdaemon instance to exit cleanly\n"
            << "  --config <path>    Path to BrBrDbTelemetry.json config\n"
            << "  --simulator <name> Target simulator: auto (default), acevo, acr, ac\n"
            << "  --league <name>    League name (defaults to simulator: acevo, acr, ac)\n"
            << "  --help, -h         Show this help message\n";
}

} // namespace

int main(int argc, char* argv[]) {
  bool headless = false;
  bool consoleMode = false;
  bool stopRequested = false;
  std::wstring configPath;
  std::string cliSimulator;
  std::string cliLeague;

  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if (arg == "--headless") {
      headless = true;
    } else if (arg == "--tray") {
      headless = false;
    } else if (arg == "--console" || arg == "-c") {
      consoleMode = true;
    } else if (arg == "--stop") {
      stopRequested = true;
    } else if (arg == "--config" && i + 1 < argc) {
      configPath = Utf8ToWide(argv[++i]);
    } else if (arg == "--simulator" && i + 1 < argc) {
      cliSimulator = argv[++i];
    } else if (arg == "--league" && i + 1 < argc) {
      cliLeague = argv[++i];
    } else if (arg == "--help" || arg == "-h") {
      ShowHelp();
      return 0;
    } else {
      std::cerr << "Unknown argument: " << arg << "\nRun with --help for usage.\n";
      return 1;
    }
  }

  // Handle --stop command
  if (stopRequested) {
    HANDLE hStopEvent = OpenEventW(EVENT_MODIFY_STATE, FALSE, L"Local\\brbrdaemon_stop_event");
    if (hStopEvent) {
      SetEvent(hStopEvent);
      CloseHandle(hStopEvent);
      std::cout << "Successfully signaled running brbrdaemon to stop.\n";
      return 0;
    } else {
      std::cout << "No active brbrdaemon process found.\n";
      return 0;
    }
  }

  // Single-instance guard
  HANDLE hSingleInstance = CreateMutexW(NULL, TRUE, L"Local\\brbrdaemon_instance_mutex");
  if (GetLastError() == ERROR_ALREADY_EXISTS) {
    if (hSingleInstance) CloseHandle(hSingleInstance);
    std::cerr << "Another instance of brbrdaemon is already running.\n";
    return 0;
  }

  // Create named stop event for graceful shutdown by installer or --stop
  HANDLE hStopEvent = CreateEventW(NULL, TRUE, FALSE, L"Local\\brbrdaemon_stop_event");

  // Console management
  if (consoleMode) {
    if (!GetConsoleWindow()) {
      AllocConsole();
      FILE* fpDummy;
      freopen_s(&fpDummy, "CONOUT$", "w", stdout);
      freopen_s(&fpDummy, "CONOUT$", "w", stderr);
    }
  } else if (!headless) {
    HWND hConsole = GetConsoleWindow();
    if (hConsole) {
      ShowWindow(hConsole, SW_HIDE);
    }
  }

  SetConsoleCtrlHandler(ConsoleCtrlHandler, TRUE);

  std::wstring moduleDir;
  try {
    moduleDir = GetCurrentModuleDirectory();
  } catch (...) {
    moduleDir = L".";
  }
  g_moduleDir = moduleDir;

  if (configPath.empty()) {
    configPath = moduleDir + L"\\BrBrDbTelemetry.json";
  }

  g_telemetryDirPath = moduleDir + L"\\Telemetry";
  g_logFilePath = moduleDir + L"\\brbrdaemon.log";

  Logger logger(g_logFilePath, consoleMode);
  logger.Write("=================================================");
  logger.Write("Starting brbrdaemon");
  logger.Write("Module Directory: " + WideToUtf8(moduleDir));

  // Stop watcher thread
  std::thread stopWatcher([hStopEvent]() {
    if (hStopEvent) {
      WaitForSingleObject(hStopEvent, INFINITE);
      g_exitRequested = true;
      if (g_hWnd) {
        PostMessageW(g_hWnd, WM_CLOSE, 0, 0);
      }
      {
        std::lock_guard<std::mutex> lock(g_exitMutex);
        g_exitCv.notify_all();
      }
    }
  });

  PluginConfig config;
  try {
    if (std::filesystem::exists(configPath)) {
      config = LoadPluginConfig(configPath);
      logger.Write("Loaded configuration from: " + WideToUtf8(configPath));
    } else {
      logger.Write("Configuration file not found, creating default: " + WideToUtf8(configPath));
      config.simulator = "auto";
      config.league = "auto";
      SinkConfig fileSink;
      fileSink.type = "file";
      fileSink.path = WideToUtf8(g_telemetryDirPath);
      fileSink.enabled = true;
      config.sinks.push_back(fileSink);

      SinkConfig httpSink;
      httpSink.type = "http";
      httpSink.serverAddress = "https://brbrdb.brbrkitten.com";
      httpSink.enabled = false;
      config.sinks.push_back(httpSink);

      SavePluginConfig(configPath, config);
    }
  } catch (const std::exception& e) {
    logger.Write(std::string("Error reading config: ") + e.what() + " - Falling back to default file sink");
    config.simulator = "auto";
    config.league = "auto";
    SinkConfig fileSink;
    fileSink.type = "file";
    fileSink.path = WideToUtf8(g_telemetryDirPath);
    fileSink.enabled = true;
    config.sinks.push_back(fileSink);
  }

  // CLI overrides
  if (!cliSimulator.empty()) {
    config.simulator = cliSimulator;
    logger.Write("Simulator overridden by CLI: " + config.simulator);
  }
  if (!cliLeague.empty()) {
    config.league = cliLeague;
    logger.Write("League overridden by CLI: " + config.league);
  }

  // Find file sink directory if specified
  for (const auto& sink : config.sinks) {
    if (sink.type == "file" && !sink.path.empty()) {
      g_telemetryDirPath = Utf8ToWide(sink.path);
      break;
    }
  }

  auto shmReader = std::make_shared<AceSharedMemoryReader>();
  TelemetryCollector collector(shmReader, config, logger, g_telemetryDirPath);
  g_pCollector = &collector;
  collector.Start();

  if (!headless) {
    HINSTANCE hInstance = GetModuleHandle(NULL);
    HICON hAppIcon = LoadIcon(hInstance, MAKEINTRESOURCE(1));
    HICON hSmallIcon = (HICON)LoadImageW(hInstance, MAKEINTRESOURCEW(1), IMAGE_ICON,
                                         GetSystemMetrics(SM_CXSMICON),
                                         GetSystemMetrics(SM_CYSMICON),
                                         LR_DEFAULTCOLOR);

    WNDCLASSEXW wc = {0};
    wc.cbSize = sizeof(WNDCLASSEXW);
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hInstance;
    wc.hIcon = hAppIcon ? hAppIcon : LoadIcon(NULL, IDI_APPLICATION);
    wc.hIconSm = hSmallIcon ? hSmallIcon : wc.hIcon;
    wc.lpszClassName = L"BrBrDaemonTrayClass";
    if (RegisterClassExW(&wc)) {
      g_hWnd = CreateWindowExW(0, L"BrBrDaemonTrayClass", L"brbrdaemon",
                               0, 0, 0, 0, 0, NULL, NULL, hInstance, NULL);
    }

    g_wmTaskbarCreated = RegisterWindowMessageW(L"TaskbarCreated");

    g_nid.cbSize = sizeof(NOTIFYICONDATAW);
    g_nid.hWnd = g_hWnd;
    g_nid.uID = 1;
    g_nid.uFlags = NIF_ICON | NIF_MESSAGE | NIF_TIP;
    g_nid.uCallbackMessage = WM_TRAYICON;
    g_nid.hIcon = hSmallIcon ? hSmallIcon : (hAppIcon ? hAppIcon : LoadIcon(NULL, IDI_APPLICATION));
    wcscpy_s(g_nid.szTip, L"BrBrDb Telemetry Daemon");
    Shell_NotifyIconW(NIM_ADD, &g_nid);

    logger.Write("System tray initialized. Running background telemetry loop...");

    MSG msg;
    while (!g_exitRequested && GetMessage(&msg, NULL, 0, 0)) {
      TranslateMessage(&msg);
      DispatchMessage(&msg);
    }

    Shell_NotifyIconW(NIM_DELETE, &g_nid);
    if (g_hWnd) {
      DestroyWindow(g_hWnd);
    }
  } else {
    logger.Write("Running in headless background mode.");
    std::unique_lock<std::mutex> lock(g_exitMutex);
    g_exitCv.wait(lock, []() { return g_exitRequested.load(); });
  }

  logger.Write("Stopping Telemetry Collector...");
  collector.Stop();
  g_pCollector = nullptr;

  if (hStopEvent) {
    SetEvent(hStopEvent); // Unblock stop watcher thread
  }
  if (stopWatcher.joinable()) {
    stopWatcher.join();
  }
  if (hStopEvent) {
    CloseHandle(hStopEvent);
  }
  if (hSingleInstance) {
    CloseHandle(hSingleInstance);
  }

  {
    std::lock_guard<std::mutex> lock(g_cleanupMutex);
    g_cleanedUp = true;
    g_cleanupCv.notify_all();
  }

  logger.Write("brbrdaemon exited cleanly.");
  return 0;
}
