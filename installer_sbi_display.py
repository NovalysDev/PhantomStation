"""Distingue un SBI présent d'une correspondance vérifiée par le registre."""
from pathlib import Path
import re

BASE = Path(__file__).resolve().parent


def install_sbi_display(project, required_serials=()):
    root = Path(project)
    header = root / 'recomp-ui/src/recomp_launcher.h'
    main = root / 'psxrecomp/runtime/src/main.cpp'
    ui = root / 'recomp-ui/src/common/backends/imgui/launcher_imgui.cpp'
    h = header.read_text(encoding='utf-8')
    m = main.read_text(encoding='utf-8')
    u = ui.read_text(encoding='utf-8')
    if 'PHANTOM_SBI_PRESENT_V1' in m:
        if 'RECOMP_SBI_PRESENT' not in h or 'RECOMP_SBI_PRESENT' not in u:
            raise ValueError('Installation SBI incomplète.')
        install_missing_sbi_support(root, required_serials)
        return
    old_enum = 'enum { RECOMP_SBI_NA = 0, RECOMP_SBI_MISSING = 1, RECOMP_SBI_OK = 2 };'
    old_verdict = ('        out->sbi_status = !companion.required ? RECOMP_SBI_NA :\n'
                   '                         companion.ready ? RECOMP_SBI_OK : RECOMP_SBI_MISSING;')
    old_text = ('        const char* sbi_text = v.sbi_status == RECOMP_SBI_OK ? "OK" :\n'
                '                              v.sbi_status == RECOMP_SBI_MISSING ? "Missing" : "N/A";\n'
                '        kv_row("SBI File", pending ? dash : sbi_text,\n'
                '               th, !pending && v.sbi_status != RECOMP_SBI_NA,\n'
                '               v.sbi_status == RECOMP_SBI_OK);')
    if h.count(old_enum) != 1 or m.count(old_verdict) != 1 or u.count(old_text) != 1:
        raise ValueError('Affichage SBI introuvable dans cette version du lanceur.')
    h = h.replace(old_enum, 'enum { RECOMP_SBI_NA = 0, RECOMP_SBI_MISSING = 1, RECOMP_SBI_OK = 2, RECOMP_SBI_PRESENT = 3 };', 1)
    m = m.replace(old_verdict, '''        // PHANTOM_SBI_PRESENT_V1: unknown registry entry is not an absent SBI.
        std::error_code sbi_error;
        auto sbi_file = resolved.mount;
        sbi_file.replace_extension(".sbi");
        const bool sbi_present = id.opened && std::filesystem::is_regular_file(sbi_file, sbi_error);
        out->sbi_status = companion.required ?
            (companion.ready ? RECOMP_SBI_OK : RECOMP_SBI_MISSING) :
            (sbi_present ? RECOMP_SBI_PRESENT : RECOMP_SBI_NA);''', 1)
    u = u.replace(old_text, '''        const char* sbi_text = v.sbi_status == RECOMP_SBI_OK ? "Vérifié" :
                              v.sbi_status == RECOMP_SBI_PRESENT ? "Présent" :
                              v.sbi_status == RECOMP_SBI_MISSING ? "Manquant" : "Non renseigné";
        kv_row("Fichier SBI", pending ? dash : sbi_text,
               th, !pending && v.sbi_status != RECOMP_SBI_NA,
               v.sbi_status == RECOMP_SBI_OK || v.sbi_status == RECOMP_SBI_PRESENT);''', 1)
    header.write_text(h, encoding='utf-8', newline='\n')
    main.write_text(m, encoding='utf-8', newline='\n')
    ui.write_text(u, encoding='utf-8', newline='\n')
    install_missing_sbi_support(root, required_serials)


def install_missing_sbi_support(root, required_serials):
    serials = sorted(set(required_serials))
    if any(not re.fullmatch(r'(?:SLES|SCES|SLUS|SCUS|SLPS|SLPM|SCPS)-\d{5}', value) for value in serials):
        raise ValueError('Référence SBI invalide.')
    names = ', '.join('"' + value + '"' for value in serials) or 'nullptr'
    template = (BASE / 'phantom_sbi_support.hpp').read_text(encoding='utf-8')
    (root / 'psxrecomp/runtime/include/phantom_sbi_support.hpp').write_text(
        template.replace('PHANTOM_SBI_REQUIRED_SERIALS', names), encoding='utf-8', newline='\n')
    main = root / 'psxrecomp/runtime/src/main.cpp'
    source = main.read_text(encoding='utf-8')
    if 'PHANTOM_SBI_MANUAL_V2' not in source:
        include = '#include "sbi_setup.h"'
        old_import = '''            const auto root = exe_dir_from_argv(g_lnch_argv0 ? g_lnch_argv0 : "") / "sbi-input";
            const auto imported = PSXRecompV4::import_sbi_setup(resolved.data, resolved.mount, sbi, root).string();'''
        new_import = '''            // PHANTOM_SBI_MANUAL_V2: install only in this game's extracted media.
            const auto root = exe_dir_from_argv(g_lnch_argv0 ? g_lnch_argv0 : "");
            const auto expected = expected_serial_for_disc(std::filesystem::path(disc), g_lnch_expected_serial);
            const auto identity = PSXRecompV4::identify_disc(disc, expected, 0, false, false, nullptr);
            const auto serial = identity.detected_serial.empty() ? expected : identity.detected_serial;
            phantom_sbi_install(root, resolved.mount, resolved.data, sbi, serial);
            const auto imported = std::string(disc);'''
        old_present = 'const bool sbi_present = id.opened && std::filesystem::is_regular_file(sbi_file, sbi_error);'
        old_status = '''        out->sbi_status = companion.required ?
            (companion.ready ? RECOMP_SBI_OK : RECOMP_SBI_MISSING) :
            (sbi_present ? RECOMP_SBI_PRESENT : RECOMP_SBI_NA);'''
        new_status = '''        const bool catalog_requires_sbi = phantom_sbi_required(serial);
        out->sbi_status = companion.required ?
            (companion.ready ? RECOMP_SBI_OK : RECOMP_SBI_MISSING) :
            (sbi_present ? RECOMP_SBI_PRESENT : (catalog_requires_sbi ? RECOMP_SBI_MISSING : RECOMP_SBI_NA));
        if (catalog_requires_sbi && !sbi_present) out->verdict = 3;'''
        changes = [(include, include + '\n#include "phantom_sbi_support.hpp"'),
                   (old_import, new_import),
                   (old_present, 'const bool sbi_present = id.opened && phantom_sbi_valid(sbi_file);'),
                   (old_status, new_status)]
        for old, new in changes:
            if source.count(old) != 1:
                raise ValueError('Ajout du SBI impossible : structure du moteur différente.')
            source = source.replace(old, new, 1)
        main.write_text(source, encoding='utf-8', newline='\n')
    ui = root / 'recomp-ui/src/common/backends/imgui/launcher_imgui.cpp'
    source = ui.read_text(encoding='utf-8')
    if 'PHANTOM_SBI_BUTTON_V2' not in source:
        anchor = '    // "Browse For Disc 2", not "Change Disc": on a multi-disc set the button'
        picker = '''    // PHANTOM_SBI_BUTTON_V2: visible only while this disc needs a companion.
    if (m->rom_present && m->verify.sbi_status == RECOMP_SBI_MISSING && m->import_sbi_cb) {
        ImGui::TextWrapped("SBI nécessaire : choisis celui correspondant à cette version du disque.");
        if (ImGui::Button("Ajouter le fichier SBI", ImVec2(availw, px(34)))) {
            const char* patterns[] = {"*.sbi"};
            PickRequest req = rom_pick_request(m, "Choisir le SBI de ce disque", patterns, 1, "Fichiers SBI", false);
            req.on_pick = [m](const char* path) {
                if (!path) return;
                char mounted[sizeof(m->rom_full)] = {};
                m->setup_error[0] = '\\0';
                if (!m->import_sbi_cb(m->rom_full, path, mounted, sizeof(mounted), m->setup_error, sizeof(m->setup_error))) return;
                launcher_model_set_rom(m, mounted);
            };
            ui_pick(m, std::move(req));
        }
        ImGui::Dummy(ImVec2(0, px(8)));
    }
'''
        suffix = '''    if (m->import_sbi_cb)
        std::strncat(change_label, " / SBI", sizeof(change_label) - std::strlen(change_label) - 1);
'''
        title = 'snprintf(title, sizeof(title), "Select %s%s", noun, m->import_sbi_cb ? " / SBI" : "");'
        headline = '''    const char* headline =
        pending          ? "No disc selected" :'''
        numbered = '''        snprintf(change_label, sizeof(change_label), "%s %s %d",
                 ui_text("Browse For"), ui_text(noun), disc_no);'''
        unnumbered = '''        snprintf(change_label, sizeof(change_label), "%s %s",
                 ui_text("Browse For"), ui_text(noun));'''
        if any(source.count(part) != 1 for part in (anchor, suffix, title, headline, numbered, unnumbered)):
            raise ValueError('Ajout du SBI impossible : structure du lanceur différente.')
        source = source.replace(anchor, picker + anchor, 1).replace(suffix, '', 1)
        source = source.replace(title, 'snprintf(title, sizeof(title), "Choisir un disque");', 1)
        source = source.replace(numbered, '        snprintf(change_label, sizeof(change_label), "Choisir le disque %d", disc_no);', 1)
        source = source.replace(unnumbered, '        snprintf(change_label, sizeof(change_label), "Choisir le disque");', 1)
        source = source.replace(headline, '''    const char* headline =
        !pending && v.sbi_status == RECOMP_SBI_MISSING ? "SBI manquant" :
        pending          ? "No disc selected" :''', 1)
        ui.write_text(source, encoding='utf-8', newline='\n')
