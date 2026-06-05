#include <flutter/dart_project.h>
#include <flutter/flutter_view_controller.h>
#include <windows.h>
#include <tlhelp32.h>

#include "flutter_window.h"
#include "utils.h"

/// Intenta matar procesos Python rezagados del backend filtrando
/// por el nombre de la ventana de consola o el directorio de trabajo.
/// Solo actúa si el proceso se lanzó desde nuestra carpeta backend.
void KillLingeringBackend() {
  // Obtener la ruta del directorio actual (raíz del proyecto)
  wchar_t currentDir[MAX_PATH];
  ::GetCurrentDirectoryW(MAX_PATH, currentDir);
  std::wstring dirStr(currentDir);

  // Solo proceder si el directorio contiene "reconocimiento-facial"
  // para evitar matar procesos Python arbitrarios.
  if (dirStr.find(L"reconocimiento-facial") == std::wstring::npos &&
      dirStr.find(L"backend") == std::wstring::npos) {
    return;
  }

  HANDLE snapshot = ::CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
  if (snapshot == INVALID_HANDLE_VALUE) return;

  PROCESSENTRY32W pe = {sizeof(pe)};
  if (::Process32FirstW(snapshot, &pe)) {
    do {
      if (_wcsicmp(pe.szExeFile, L"python.exe") == 0 ||
          _wcsicmp(pe.szExeFile, L"python3.exe") == 0) {
        // Verificar el directorio de trabajo del proceso abriendo
        // un handle de consulta
        HANDLE hProc = ::OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_TERMINATE | PROCESS_VM_READ,
            FALSE, pe.th32ProcessID);
        if (hProc) {
          // Intentar terminar el proceso (limitado a los que podemos
          // abrir - normalmente los nuestros)
          ::TerminateProcess(hProc, 1);
          ::CloseHandle(hProc);
        }
      }
    } while (::Process32NextW(snapshot, &pe));
  }
  ::CloseHandle(snapshot);
}

int APIENTRY wWinMain(_In_ HINSTANCE instance, _In_opt_ HINSTANCE prev,
                      _In_ wchar_t *command_line, _In_ int show_command) {
  // Attach to console when present (e.g., 'flutter run') or create a
  // new console when running with a debugger.
  if (!::AttachConsole(ATTACH_PARENT_PROCESS) && ::IsDebuggerPresent()) {
    CreateAndAttachConsole();
  }

  // Limpiar procesos Python rezagados de ejecuciones anteriores
  KillLingeringBackend();

  // Initialize COM, so that it is available for use in the library and/or
  // plugins.
  ::CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

  flutter::DartProject project(L"data");

  std::vector<std::string> command_line_arguments =
      GetCommandLineArguments();

  project.set_dart_entrypoint_arguments(std::move(command_line_arguments));

  FlutterWindow window(project);
  Win32Window::Point origin(10, 10);
  Win32Window::Size size(1280, 720);
  if (!window.Create(L"BioFace - Reconocimiento Facial", origin, size)) {
    return EXIT_FAILURE;
  }
  window.SetQuitOnClose(true);

  ::MSG msg;
  while (::GetMessage(&msg, nullptr, 0, 0)) {
    ::TranslateMessage(&msg);
    ::DispatchMessage(&msg);
  }

  ::CoUninitialize();
  return EXIT_SUCCESS;
}
