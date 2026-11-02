"""Sélectionne une manette détectée pour le joueur 1 dans le lanceur PS1."""
from pathlib import Path


def install_controller_auto(project):
    root = Path(project)
    ui = root / 'recomp-ui/src/common/backends/imgui/launcher_imgui.cpp'
    source = ui.read_text(encoding='utf-8')
    template = Path(__file__).with_name('phantom_controller_auto.hpp')
    target = ui.parent / 'phantom_controller_auto.hpp'
    if 'PHANTOM_CONTROLLER_AUTO_V1' not in source:
        declarations = 'void draw_source_selectables(LauncherModel* m, int p) {'
        first = '    bool first_present_marked = false;'
        sync = '            launcher_binds_sync_psx_pad_sources(m, g_pads, g_pad_count);'
        if any(source.count(anchor) != 1 for anchor in (declarations, first, sync)):
            raise ValueError('Sélection automatique de manette impossible dans ce lanceur.')
        source = source.replace(declarations, '#include "phantom_controller_auto.hpp"\n\n' + declarations, 1)
        source = source.replace(first, first + '\n    bool phantom_controller_auto_done = false; // PHANTOM_CONTROLLER_AUTO_V1', 1)
        source = source.replace(sync, sync + '\n            if (!phantom_controller_auto_done)\n'
            '                phantom_controller_auto_done = phantom_select_first_controller(m, g_pads, g_pad_count);', 1)
    target.write_text(template.read_text(encoding='utf-8'), encoding='utf-8', newline='\n')
    ui.write_text(source, encoding='utf-8', newline='\n')
