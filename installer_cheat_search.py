"""Installe la recherche de pistes de codes pendant la partie."""
from pathlib import Path
import shutil

BASE = Path(__file__).resolve().parent
MARKER = 'PHANTOM_CHEAT_SEARCH_V1'
ITEMS = '''    {"search.life", "Recherche de codes", "Nouvelle recherche : vie", "Reprendre, perdre de la vie puis comparer.", RECOMP_RUNTIME_UI_ACTION, 0, 0, 0, NULLPTR, 0, NULLPTR},
    {"search.ammo", "Recherche de codes", "Nouvelle recherche : munitions", "Reprendre, tirer puis comparer.", RECOMP_RUNTIME_UI_ACTION, 0, 0, 0, NULLPTR, 0, NULLPTR},
    {"search.map", "Recherche de codes", "Nouvelle recherche : carte", "Reprendre, changer de zone puis comparer.", RECOMP_RUNTIME_UI_ACTION, 0, 0, 0, NULLPTR, 0, NULLPTR},
    {"search.weapon", "Recherche de codes", "Nouvelle recherche : arme", "Reprendre, changer d'arme puis comparer.", RECOMP_RUNTIME_UI_ACTION, 0, 0, 0, NULLPTR, 0, NULLPTR},
    {"search.less", "Recherche de codes", "La valeur a diminue", "Conserver seulement les valeurs qui ont baisse.", RECOMP_RUNTIME_UI_ACTION, 0, 0, 0, NULLPTR, 0, NULLPTR},
    {"search.more", "Recherche de codes", "La valeur a augmente", "Conserver seulement les valeurs qui ont augmente.", RECOMP_RUNTIME_UI_ACTION, 0, 0, 0, NULLPTR, 0, NULLPTR},
    {"search.changed", "Recherche de codes", "La valeur a change", "Conserver seulement les valeurs differentes.", RECOMP_RUNTIME_UI_ACTION, 0, 0, 0, NULLPTR, 0, NULLPTR},
    {"search.same", "Recherche de codes", "La valeur est restee identique", "Conserver seulement les valeurs inchangees.", RECOMP_RUNTIME_UI_ACTION, 0, 0, 0, NULLPTR, 0, NULLPTR},
    {"search.propose", "Recherche de codes", "Proposer les pistes restantes", "Jusqu'a 10 pistes non verifiees ; aucune n'est activee.", RECOMP_RUNTIME_UI_ACTION, 0, 0, 0, NULLPTR, 0, NULLPTR},
'''
KEYS = '"search.life", "search.ammo", "search.map", "search.weapon", "search.less", "search.more", "search.changed", "search.same", "search.propose"'


def once(source, old, new, description):
    if source.count(old) != 1:
        raise ValueError('Recherche de codes : emplacement introuvable : ' + description)
    return source.replace(old, new, 1)


def install_cheat_search(project):
    root = Path(project)
    inc = root / 'psxrecomp/runtime/include'
    for name in ('phantom_cheat_search.hpp', 'phantom_cheat_search_runtime.hpp'):
        shutil.copyfile(BASE / name, inc / name)
    main = root / 'psxrecomp/runtime/src/main.cpp'
    source = main.read_text(encoding='utf-8')
    if MARKER not in source:
        source = once(source, 'static void mod_call_frame_hooks() {',
                      '// ' + MARKER + '\n#include "phantom_cheat_search_runtime.hpp"\n\nstatic void mod_call_frame_hooks() {', 'moteur')
        source = once(source, '    phantom_codes_tick();', '    phantom_codes_tick();\n    phantom_cheat_search_tick();', 'observation')
        main.write_text(source, encoding='utf-8', newline='\n')

    provider = root / 'recomp-ui/src/consoles/psx/psx_menu_provider.cpp'
    source = provider.read_text(encoding='utf-8')
    if MARKER not in source:
        source = once(source, 'namespace rui_psx {',
                      '// ' + MARKER + '\nextern "C" int phantom_cheat_search_command(int, char*, int);\n\nnamespace rui_psx {', 'menu')
        source = once(source, '\n};\n\nbool is(const RecompRuntimeUiItem', '\n' + ITEMS.replace('NULLPTR', 'nullptr') + '};\n\nbool is(const RecompRuntimeUiItem', 'actions du menu')
        source = once(source, '    } else if (is(item, "media.insert")) {', '''    } else if (item && item->key && !std::strncmp(item->key, "search.", 7)) {
        const char* keys[] = {''' + KEYS + '''};
        for (int i = 0; i < 9; ++i) if (!std::strcmp(item->key, keys[i])) {
            char message[160] = {}; phantom_cheat_search_command(i, message, sizeof message); status(message); break;
        }
    } else if (is(item, "media.insert")) {''', 'choix du menu')
        source = once(source, '    if (item && item->key && !std::strncmp(item->key, "media.", 6))',
                      '    if (item && item->key && !std::strncmp(item->key, "search.", 7)) return !netplay();\n    if (item && item->key && !std::strncmp(item->key, "media.", 6))', 'protection en ligne')
        source = once(source, '    if (host->default_selection && (!host->disc_count || host->disc_count() < 2)) return 0;',
                      '    // La recherche est aussi disponible pour les jeux a un seul disque.', 'jeux a un disque')
        source = once(source, '    cfg.extra_item_count = discs > 1 ? 3 : 1;',
                      '    cfg.extra_item_count = sizeof(g_extra) / sizeof(g_extra[0]);', 'nombre de rubriques')
        provider.write_text(source, encoding='utf-8', newline='\n')

    fallback = root / 'psxrecomp/runtime/src/psx_runtime_menu.c'
    source = fallback.read_text(encoding='utf-8')
    if MARKER not in source:
        source = once(source, '#include "recomp_runtime_ui.h"', '#include "recomp_runtime_ui.h"\n/* ' + MARKER + ' */\nextern int phantom_cheat_search_command(int, char*, int);', 'menu logiciel')
        source = once(source, '    {"system.resume", "Game", "Resume",', ITEMS.replace('NULLPTR', 'NULL') + '    {"system.resume", "Game", "Resume",', 'actions logiciel')
        source = once(source, '    return !strncmp(item->key, "media.", 6) ?',
                      '    if (!strncmp(item->key, "search.", 7)) return !psx_netplay_active();\n    return !strncmp(item->key, "media.", 6) ?', 'protection logiciel')
        source = once(source, '    } else {\n        recomp_runtime_ui_close(s_ui);\n    }\n    return 0; /* Keep the specific error', '''    } else if (!strncmp(item->key, "search.", 7)) {
        const char* keys[] = {''' + KEYS + '''};
        for (int i = 0; i < 9; ++i) if (!strcmp(item->key, keys[i])) {
            phantom_cheat_search_command(i, s_status, sizeof s_status);
            recomp_runtime_ui_set_status(s_ui, s_status); break;
        }
    } else {
        recomp_runtime_ui_close(s_ui);
    }
    return 0; /* Keep the specific error''', 'choix logiciel')
        source = once(source, '    if (psx_disc_tray_disc_count() < 2) return;\n    s_items[0].maximum = psx_disc_tray_disc_count();',
                      '    s_items[0].maximum = psx_disc_tray_disc_count() > 1 ? psx_disc_tray_disc_count() : 1;', 'jeu a un disque')
        fallback.write_text(source, encoding='utf-8', newline='\n')

    ui = root / 'recomp-ui/src/common/backends/imgui/launcher_imgui.cpp'
    source = ui.read_text(encoding='utf-8')
    if MARKER not in source:
        source = once(source, '    static std::vector<phantom_codes::Cheat> codes;\n    static bool loaded = false;',
            '    static std::vector<phantom_codes::Cheat> codes;\n    static std::vector<phantom_codes::Cheat> suggestions; // ' + MARKER + '\n    static bool loaded = false;', 'page Codes')
        source = once(source, '    if (!loaded) { load_ok=phantom_codes::load(codes,error); loaded=true; }',
            '''    if (!loaded) {
        load_ok=phantom_codes::load(codes,error);
        std::string suggestion_error;
        if (std::filesystem::exists("phantom-codes-suggestions.tsv"))
            (void)phantom_codes::load_file("phantom-codes-suggestions.tsv", suggestions, suggestion_error);
        loaded=true;
    }''', 'chargement des pistes')
        source = once(source, '        int remove = -1;\n        for (size_t i=0; i<codes.size(); ++i) {',
            '''        if (!suggestions.empty()) {
            ImGui::Separator(); ImGui::TextWrapped("Pistes detectees en jeu : non verifiees, a tester individuellement.");
            for (size_t i = 0; i < suggestions.size(); ++i) {
                auto& candidate = suggestions[i];
                bool already = false;
                for (const auto& saved : codes) if (saved.lines == candidate.lines) { already = true; break; }
                ImGui::PushID(static_cast<int>(i + 10000));
                ImGui::TextWrapped("%s", candidate.name.c_str()); ImGui::SameLine();
                if (already) ImGui::TextDisabled("Ajoutee");
                else if (ImGui::SmallButton("Ajouter la piste")) {
                    if (codes.size() >= 200 || !candidate.error.empty()) error="Piste invalide ou limite de codes atteinte.";
                    else {
                        auto next = codes; candidate.enabled = false; next.push_back(candidate); std::string save_error;
                        if (phantom_codes::save(next, save_error)) { codes=std::move(next); error.clear(); }
                        else error="Enregistrement impossible : " + save_error;
                    }
                }
                ImGui::PopID();
            }
        }
        int remove = -1;
        for (size_t i=0; i<codes.size(); ++i) {''', 'affichage des pistes')
        ui.write_text(source, encoding='utf-8', newline='\n')
    source = source.replace('avant de cliquer sur Play.', 'avant de cliquer sur Jouer.')
    ui.write_text(source, encoding='utf-8', newline='\n')
