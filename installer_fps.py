"""Expose the engine's built-in FPS display in the game's settings."""
from pathlib import Path

MARKER = 'PHANTOM_FPS_SETTING_V1'

RUNTIME_OLD = '''        s_fps_telemetry_enabled = (e && e[0] && e[0] != '0') ? 1 : 0;'''
RUNTIME_NEW = r'''        // PHANTOM_FPS_SETTING_V1: the launcher's saved choice takes priority.
        s_fps_telemetry_enabled = (e && e[0] && e[0] != '0') ? 1 : 0;
        if (FILE* f = std::fopen("phantom-fps.txt", "rb")) {
            int choice = std::fgetc(f);
            std::fclose(f);
            if (choice == '0' || choice == '1') s_fps_telemetry_enabled = choice == '1';
        }'''

UI_ANCHOR = '''        if (m->mod_status[0]) ImGui::TextColored(col(th.warn), "%s", m->mod_status);'''
UI = r'''        // PHANTOM_FPS_SETTING_V1: same per-game file is read by the runtime.
        ImGui::Spacing();
        eyebrow("PERFORMANCES");
        static bool phantom_fps_loaded = false;
        static bool phantom_fps_enabled = false;
        if (!phantom_fps_loaded) {
            const char* env = std::getenv("PSX_FPS_TELEMETRY");
            phantom_fps_enabled = env && *env && *env != '0';
            if (FILE* f = std::fopen("phantom-fps.txt", "rb")) {
                int choice = std::fgetc(f);
                std::fclose(f);
                if (choice == '0' || choice == '1') phantom_fps_enabled = choice == '1';
            }
            phantom_fps_loaded = true;
        }
        if (ImGui::Checkbox("Afficher les FPS pendant le jeu", &phantom_fps_enabled)) {
            std::string error;
            if (!psx::atomic_write_file("phantom-fps.txt", phantom_fps_enabled ? "1\n" : "0\n", &error)) {
                phantom_fps_enabled = !phantom_fps_enabled;
                std::snprintf(m->mod_status, sizeof(m->mod_status), "Impossible d'enregistrer l'affichage FPS.");
            }
        }
        ImGui::TextWrapped("Affiche les images par seconde et la vitesse du jeu. La touche F permet aussi de l'activer en jeu.");
'''


def install_fps_setting(project):
    project = Path(project)
    main = project / 'psxrecomp/runtime/src/main.cpp'
    source = main.read_text(encoding='utf-8')
    if MARKER not in source:
        if source.count(RUNTIME_OLD) != 1:
            raise ValueError("L'affichage FPS du moteur n'a pas été reconnu.")
        main.write_text(source.replace(RUNTIME_OLD, RUNTIME_NEW, 1), encoding='utf-8', newline='\n')
    ui = project / 'recomp-ui/src/common/backends/imgui/launcher_imgui.cpp'
    source = ui.read_text(encoding='utf-8')
    if MARKER not in source:
        include = '#include "launcher_backend.h"'
        if source.count(include) != 1 or source.count(UI_ANCHOR) != 1:
            raise ValueError('La page de réglages FPS a changé.')
        source = source.replace(include, '#include "atomic_file_write.h"\n' + include, 1)
        source = source.replace(UI_ANCHOR, UI + UI_ANCHOR, 1)
        ui.write_text(source, encoding='utf-8', newline='\n')
