"""Traduit le bouton de démarrage et rend visible son enfoncement."""
from pathlib import Path


def install_play_button(project):
    path = Path(project) / 'recomp-ui/src/common/backends/imgui/launcher_imgui.cpp'
    source = path.read_text(encoding='utf-8')
    if 'PHANTOM_PLAY_PRESS_V1' in source:
        return
    main = 'neon_cta("##play", ui_text(m->in_session ? "RESUME" : "PLAY"),'
    lobby = 'neon_cta("##lobby_play", ui_text("PLAY"),'
    active = '    bool act = ImGui::IsItemActive();\n    bool foc = ImGui::IsItemFocused();'
    gradient = '    grad_rect(dl, mn, mx, r, top, bot);\n    dl->AddRect(mn, mx, imcol(th.accent, hov ? 0.9f : 0.5f),'
    center = '    float cx = p.x + (size.x - total) * 0.5f, cy = p.y + size.y * 0.5f;'
    if any(source.count(item) != 1 for item in (main, lobby, active, gradient, center)):
        raise ValueError('Bouton Jouer introuvable dans cette version du lanceur.')
    source = source.replace(main, 'neon_cta("##play", m->in_session ? ui_text("RESUME") : "Jouer",', 1)
    source = source.replace(lobby, 'neon_cta("##lobby_play", "Jouer",', 1)
    source = source.replace(active, '    bool act = ImGui::IsItemActive();\n'
        '    // PHANTOM_PLAY_PRESS_V1: a clear pressed state for mouse and gamepad.\n'
        '    bool phantom_pressed = enabled && act && (std::strcmp(id, "##play") == 0 || std::strcmp(id, "##lobby_play") == 0);\n'
        '    bool foc = ImGui::IsItemFocused();', 1)
    source = source.replace(gradient, '    grad_rect(dl, mn, mx, r, top, bot);\n'
        '    if (phantom_pressed) dl->AddRectFilled(mn, mx, IM_COL32(0, 12, 40, 105), r);\n'
        '    dl->AddRect(mn, mx, imcol(th.accent, hov ? 0.9f : 0.5f),', 1)
    source = source.replace(center, center + '\n    if (phantom_pressed) cy += px(2.0f);', 1)
    path.write_text(source, encoding='utf-8', newline='\n')
