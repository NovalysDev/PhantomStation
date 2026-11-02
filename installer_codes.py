"""Installe les codes Action Replay dans les lanceurs des jeux PS1."""
from pathlib import Path
import shutil

BASE = Path(__file__).resolve().parent
MARKER = 'PHANTOM_CHEATS_V1'

RUNTIME = r'''
// PHANTOM_CHEATS_V1: apply enabled, validated codes after game entry only.
#include "dirty_ram_interp.h"
static void phantom_codes_tick() {
    if (!fntrace_is_game_started() || psx_netplay_active()) return;
    static std::vector<phantom_codes::Cheat> codes;
    static bool loaded = false;
    static unsigned frame = 0;
    if (!loaded) {
        std::string error;
        if (!phantom_codes::load(codes, error))
            std::fprintf(stderr, "PhantomStation codes: %s\n", error.c_str());
        loaded = true;
    }
    const bool scan = (++frame % 30u) == 1u;
    auto read = [](uint32_t a) { return psx_mod_read_byte(0x80000000u | a); };
    auto write = [](uint32_t a, uint8_t v) {
        psx_mod_write_byte(0x80000000u | a, v);
        dirty_ram_mark_executable_range(a & ~3u, 4u);
    };
    for (const auto& code : codes)
        if (code.enabled && code.error.empty()) phantom_codes::apply(code, read, write, scan);
}
'''

UI = r'''
// PHANTOM_CHEATS_V1: UI and game use the same validated per-game file.
static bool phantom_codes_view = false;
static void phantom_codes_page(LauncherModel* m, const LauncherTheme& th) {
    static std::vector<phantom_codes::Cheat> codes;
    static bool loaded = false;
    static bool load_ok = true;
    static std::string error;
    static char name[121] = {};
    static char text[8192] = {};
    if (!loaded) { load_ok=phantom_codes::load(codes,error); loaded=true; }
    if (begin_panel("phantom-codes", 0, false)) {
        eyebrow("CODES ACTION REPLAY / GAMESHARK");
        ImGui::TextWrapped("Codes pour ce jeu et sa version. Coche ceux que tu veux utiliser avant de cliquer sur Jouer.");
        ImGui::TextWrapped("Les codes peuvent modifier la partie et ses sauvegardes. Les formats non reconnus restent désactivés.");
        if (!error.empty()) ImGui::TextColored(col(th.warn), "%s", error.c_str());
        if (!load_ok) ImGui::BeginDisabled();
        if (codes.empty()) ImGui::TextDisabled("Aucun code trouvé pour cette référence. Tu peux en ajouter un ci-dessous.");
        int remove = -1;
        for (size_t i=0; i<codes.size(); ++i) {
            auto& code=codes[i]; ImGui::PushID(static_cast<int>(i));
            bool enabled=code.enabled;
            if (!code.error.empty()) ImGui::BeginDisabled();
            if (ImGui::Checkbox(code.name.c_str(), &enabled)) {
                bool old=code.enabled; code.enabled=enabled;
                std::string save_error;
                if (!phantom_codes::save(codes,save_error)) { code.enabled=old; error="Enregistrement impossible : "+save_error; }
                else error.clear();
            }
            if (!code.error.empty()) { ImGui::EndDisabled(); ImGui::TextDisabled("Non pris en charge : %s",code.error.c_str()); }
            ImGui::SameLine();
            if (ImGui::SmallButton("Supprimer")) remove=static_cast<int>(i);
            ImGui::PopID();
        }
        if (remove>=0) {
            auto next=codes; next.erase(next.begin()+remove); std::string save_error;
            if (phantom_codes::save(next,save_error)) { codes=std::move(next); error.clear(); }
            else error="Enregistrement impossible : "+save_error;
        }
        ImGui::Separator(); ImGui::Spacing(); eyebrow("AJOUTER UN CODE");
        ImGui::TextUnformatted("Nom du code"); ImGui::SetNextItemWidth(ImGui::GetContentRegionAvail().x);
        ImGui::InputText("##phantom-code-name",name,sizeof(name));
        ImGui::TextUnformatted("Code Action Replay ou GameShark, une ligne par code");
        ImGui::SetNextItemWidth(ImGui::GetContentRegionAvail().x);
        ImGui::InputTextMultiline("##phantom-code-lines",text,sizeof(text),ImVec2(-1,px(120)));
        if (ImGui::Button("Ajouter le code")) {
            phantom_codes::Cheat c; c.name=phantom_codes::trim(name);
            std::istringstream lines(text); std::string line;
            while (std::getline(lines,line)) if (!phantom_codes::trim(line).empty()) c.lines.push_back(phantom_codes::trim(line));
            if (c.name.empty() || c.name.find_first_of("\t\r\n|")!=std::string::npos) error="Indique un nom de code valide.";
            else if (codes.size()>=200) error="La limite de 200 codes est atteinte.";
            else if (!phantom_codes::parse(c)) error=c.error;
            else {
                auto next=codes; next.push_back(c); std::string save_error;
                if (phantom_codes::save(next,save_error)) { codes=std::move(next); name[0]=0; text[0]=0; error.clear(); }
                else error="Enregistrement impossible : "+save_error;
            }
        }
        ImGui::TextDisabled("Les changements s'appliquent au prochain lancement.");
        if (!load_ok) ImGui::EndDisabled();
    }
    end_panel();
}
'''


def install_codes(project):
    project = Path(project)
    include = project / 'psxrecomp/runtime/include/phantom_codes.hpp'
    include.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BASE / 'phantom_codes.hpp', include)
    main = project / 'psxrecomp/runtime/src/main.cpp'
    source = main.read_text(encoding='utf-8')
    if MARKER not in source:
        include_anchor = '#include "audio_sync.h"'
        hook_anchor = 'static void mod_call_frame_hooks() {'
        invoke = '    mod_call_frame_hooks();'
        if source.count(include_anchor) != 1 or source.count(hook_anchor) != 1 or source.count(invoke) != 1:
            raise ValueError('Structure du moteur changée : codes Action Replay impossibles à intégrer.')
        source = source.replace(include_anchor, '#include "phantom_codes.hpp"\n' + include_anchor, 1)
        source = source.replace(hook_anchor, RUNTIME + '\n' + hook_anchor, 1)
        source = source.replace(invoke, invoke + '\n    phantom_codes_tick();', 1)
        main.write_text(source, encoding='utf-8', newline='\n')
    ui = project / 'recomp-ui/src/common/backends/imgui/launcher_imgui.cpp'
    source = ui.read_text(encoding='utf-8')
    if MARKER not in source:
        include_anchor = '#include "launcher_backend.h"'
        helper_anchor = 'void draw_settings(LauncherModel* m, const LauncherTheme& th) {'
        count_anchor = 'const int count = 1 + (m->mods ? 1 : 0) +'
        settings_button = 'if (ImGui::Button(ui_text("Réglages"), ImVec2(w, px(34))))\n                launcher_model_set_view(m, LNG_VIEW_SETTINGS);'
        view = 'case LNG_VIEW_SETTINGS:   draw_settings(m, th);             break;'
        back = 'if (ImGui::Button(ui_text("< Retour"), ImVec2(w, px(34)))) {'
        if any(source.count(x) != 1 for x in (include_anchor,helper_anchor,count_anchor,settings_button,view,back)):
            raise ValueError('Structure du lanceur changée : rubrique des codes impossible à intégrer.')
        source = source.replace(include_anchor, '#include "phantom_codes.hpp"\n' + include_anchor, 1)
        source = source.replace(helper_anchor, UI + '\n' + helper_anchor, 1)
        source = source.replace(count_anchor, 'const int count = 2 + (m->mods ? 1 : 0) +', 1)
        source = source.replace(settings_button, '''if (ImGui::Button(ui_text("Réglages"), ImVec2(w, px(34)))) {
                phantom_codes_view=false; launcher_model_set_view(m, LNG_VIEW_SETTINGS);
            }
            ImGui::SameLine(0, gap);
            if (ImGui::Button("Codes", ImVec2(w, px(34)))) {
                phantom_codes_view=true; launcher_model_set_view(m, LNG_VIEW_SETTINGS);
            }''', 1)
        source = source.replace(view, 'case LNG_VIEW_SETTINGS:   if (phantom_codes_view) phantom_codes_page(m, th); else draw_settings(m, th); break;', 1)
        source = source.replace(back, back + '\n                    phantom_codes_view=false;', 1)
        ui.write_text(source, encoding='utf-8', newline='\n')
