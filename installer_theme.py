"""Habillage du lanceur de chaque jeu avec ses visuels locaux."""
from pathlib import Path


def install_game_theme(project):
    path = Path(project) / 'recomp-ui/src/common/backends/imgui/launcher_imgui.cpp'
    text = path.read_text(encoding='utf-8')
    if 'g_phantom_background' in text:
        return
    declarations = 'LauncherTexture g_boxart, g_pad, g_pad_analog, g_pad_digital, g_brand, g_memcard;'
    panel = 'style.Colors[ImGuiCol_ChildBg]         = col(th.panel);'
    backdrop = '    // a soft violet bloom behind the header (arcade marquee glow)'
    load = '    // Controller art comes from the active SystemProfile\'s ControllerSpec —'
    release = '    launcher_texture_free(&g_boxart);'
    anchors = (declarations, panel, backdrop, load, release)
    if any(anchor not in text for anchor in anchors):
        raise ValueError('La structure du lanceur a changé : impossible d’ajouter le fond du jeu.')
    text = text.replace(declarations, declarations + '\nLauncherTexture g_phantom_background;', 1)
    text = text.replace(panel, panel + '\n    style.Colors[ImGuiCol_ChildBg].w = 0.86f;', 1)
    background = '''    if (g_phantom_background.id) {
        bg->AddImage(tid(g_phantom_background), origin,
                     ImVec2(origin.x + size.x, origin.y + size.y));
    }
'''
    text = text.replace(backdrop, background + backdrop, 1)
    text = text.replace(load, '    g_phantom_background = launcher_texture_load(asset("assets/img/phantom-background.tga").c_str());\n' + load, 1)
    text = text.replace(release, release + '\n    launcher_texture_free(&g_phantom_background);', 1)
    path.write_text(text, encoding='utf-8', newline='\n')
