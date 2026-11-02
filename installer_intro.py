"""Intègre l'introduction dans la fenêtre native du jeu après Jouer."""
from pathlib import Path

HELPER = r'''
// PHANTOM_INTRO_V4: display the existing intro inside the actual game window.
#ifdef _WIN32
#include <process.h>
#if !defined(PSX_SDL3)
#include <SDL_syswm.h>
#endif
static void phantom_intro_after_play(SDL_Window* window, int width, int height, bool fullscreen) {
    HWND parent = nullptr;
#if defined(PSX_SDL3)
    parent = (HWND)SDL_GetPointerProperty(SDL_GetWindowProperties(window), SDL_PROP_WINDOW_WIN32_HWND_POINTER, nullptr);
#else
    SDL_SysWMinfo info;
    SDL_VERSION(&info.version);
    if (SDL_GetWindowWMInfo(window, &info) && info.subsystem == SDL_SYSWM_WINDOWS)
        parent = info.info.win.window;
#endif
    const std::wstring width_arg = std::to_wstring(width);
    const std::wstring height_arg = std::to_wstring(height);
    const std::wstring parent_arg = std::to_wstring((uintptr_t)parent);
    const wchar_t* mode = fullscreen ? L"fullscreen" : L"window";
    wchar_t* program = nullptr;
    size_t size = 0;
    intptr_t child = -1;
    if (_wdupenv_s(&program, &size, L"PHANTOM_SPLASH_EXE") == 0 && program && *program) {
        const wchar_t* args[] = {program, L"--intro", width_arg.c_str(), height_arg.c_str(), mode, parent_arg.c_str(), nullptr};
        child = _wspawnv(_P_NOWAIT, program, args);
    }
    free(program);
    if (child == -1) {
        const wchar_t* local = L"phantom-intro.exe";
        const wchar_t* args[] = {local, L"--intro", width_arg.c_str(), height_arg.c_str(), mode, parent_arg.c_str(), nullptr};
        child = _wspawnv(_P_NOWAIT, local, args);
    }
    if (child == -1) return;
    HMENU menu = parent ? GetSystemMenu(parent, FALSE) : nullptr;
    UINT old_close = menu ? GetMenuState(menu, SC_CLOSE, MF_BYCOMMAND) : (UINT)-1;
    if (menu && old_close != (UINT)-1) EnableMenuItem(menu, SC_CLOSE, MF_BYCOMMAND | MF_GRAYED);
    while (WaitForSingleObject((HANDLE)child, 0) == WAIT_TIMEOUT) {
        MsgWaitForMultipleObjects(1, (HANDLE*)&child, FALSE, 30, QS_ALLINPUT);
        MSG message;
        while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
            if (message.message == WM_CLOSE && message.hwnd == parent) continue;
            if (message.message == WM_QUIT) continue;
            TranslateMessage(&message);
            DispatchMessageW(&message);
        }
    }
    CloseHandle((HANDLE)child);
    if (menu && old_close != (UINT)-1)
        EnableMenuItem(menu, SC_CLOSE, MF_BYCOMMAND | ((old_close & MF_GRAYED) ? MF_GRAYED : MF_ENABLED));
}
#else
static void phantom_intro_after_play(SDL_Window*, int, int, bool) {}
#endif
'''


def install_intro_hook(project):
    main = Path(project) / 'psxrecomp' / 'runtime' / 'src' / 'main.cpp'
    source = main.read_text(encoding='utf-8')
    if 'PHANTOM_INTRO_V4' in source:
        return
    # Upgrade projects compiled with older versions, without leaving two intros.
    if 'PHANTOM_INTRO_V3' in source:
        start = source.rfind('// PHANTOM_INTRO_V3:', 0, source.index('static void phantom_intro_after_play'))
        end = source.index('#endif', source.index('static void phantom_intro_after_play')) + len('#endif')
        if start < 0:
            raise ValueError('Introduction précédente introuvable.')
        source = source[:start] + source[end:]
        old_call = ('    int phantom_intro_width = g_video_win_w, phantom_intro_height = 0;\n'
                    '    clamp_window_aspect(&phantom_intro_width, &phantom_intro_height, g_video_aspect_num, g_video_aspect_den);\n'
                    '    phantom_intro_after_play(phantom_intro_width, phantom_intro_height, g_fullscreen != 0);\n')
        if source.count(old_call) != 1:
            raise ValueError('Ancien emplacement de l’introduction introuvable.')
        source = source.replace(old_call, '', 1)
    elif 'PHANTOM_INTRO_V2' in source:
        function = source.index('static void phantom_intro_after_play()')
        start = source.rfind('// PHANTOM_INTRO_V2:', 0, function)
        end = source.index('#endif', function) + len('#endif')
        if start < 0 or source.count('    phantom_intro_after_play();') != 1:
            raise ValueError('Ancienne introduction V2 introuvable.')
        source = source[:start] + source[end:]
        source = source.replace('    phantom_intro_after_play();', '', 1)
    main_anchor = 'int main(int argc, char** argv) {'
    if source.count(main_anchor) != 1:
        raise ValueError('Point d’entrée du moteur introuvable.')
    source = source.replace(main_anchor, HELPER + '\n' + main_anchor, 1)
    anchor = '        std::fprintf(stderr, "Fullscreen setup failed: %s\\n", SDL_GetError());'
    if source.count(anchor) != 1:
        raise ValueError('Fenêtre du jeu introuvable : introduction non installée.')
    call = ('\n\n    if (!g_hidden_window)\n'
            '        phantom_intro_after_play(sdl_window, game_w, game_h, g_fullscreen != 0);')
    source = source.replace(anchor, anchor + call, 1)
    main.write_text(source, encoding='utf-8', newline='\n')
