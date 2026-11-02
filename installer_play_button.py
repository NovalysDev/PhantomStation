"""Traduit le bouton de démarrage et rend visible son enfoncement."""
from pathlib import Path
import json


PS1_ENGLISH = {
    'Jouer': 'Play', 'Réglages': 'Settings', 'Vérifié': 'Verified', 'Présent': 'Present',
    'Manquant': 'Missing', 'Non renseigné': 'Not specified', 'Fichier SBI': 'SBI file',
    'Impossible d\'enregistrer ce réglage.': 'Unable to save this setting.',
    "QUALITE DE L'IMAGE": 'IMAGE QUALITY',
    "Choisis la netteté de l'image. Une valeur plus élevée demande plus de puissance.": 'Choose the image sharpness. Higher values require more processing power.',
    'Résolution interne du jeu': 'Internal game resolution', 'Réglage actuel : %s': 'Current setting: %s',
    "Lisser l'image affichée": 'Smooth the displayed image', 'Net': 'Sharp', 'Doux': 'Soft',
    'Contours adoucis': 'Smoothed edges', 'Lissage des textures 3D': '3D texture filtering',
    'CORRECTION 3D': '3D CORRECTION', 'Réduire les tremblements et déformations (PGXP)': 'Reduce jitter and distortion (PGXP)',
    "Si l'image présente des défauts, désactive cette correction.": 'Disable this correction if the image has visual defects.',
    'AFFICHAGE': 'DISPLAY', 'Synchronisation verticale (VSync)': 'Vertical synchronization (VSync)',
    'PERFORMANCES': 'PERFORMANCE', 'Afficher les FPS pendant le jeu': 'Show FPS during gameplay',
    "Impossible d'enregistrer l'affichage FPS.": 'Unable to save the FPS setting.',
    "Affiche les images par seconde et la vitesse du jeu. La touche F permet aussi de l'activer en jeu.": 'Shows frames per second and game speed. Press F in game to toggle it.',
    "Réglages enregistrés ; ils s'appliquent au prochain lancement.": 'Settings saved; they apply on the next launch.',
    'Codes pour ce jeu et sa version. Coche ceux que tu veux utiliser avant de cliquer sur Jouer.': 'Codes for this game and version. Select the codes to use before clicking Play.',
    'Les codes peuvent modifier la partie et ses sauvegardes. Les formats non reconnus restent désactivés.': 'Codes can change the game and its saves. Unsupported formats remain disabled.',
    'Aucun code trouvé pour cette référence. Tu peux en ajouter un ci-dessous.': 'No codes were found for this version. You can add one below.',
    'Pistes detectees en jeu : non verifiees, a tester individuellement.': 'Candidates detected in game: unverified; test them individually.',
    'Ajoutee': 'Added', 'Ajouter la piste': 'Add candidate', 'Piste invalide ou limite de codes atteinte.': 'Invalid candidate or code limit reached.',
    'Enregistrement impossible : ': 'Unable to save: ', 'Non pris en charge : %s': 'Unsupported: %s',
    'Supprimer': 'Remove', 'AJOUTER UN CODE': 'ADD A CODE', 'Nom du code': 'Code name',
    'Code Action Replay ou GameShark, une ligne par code': 'Action Replay or GameShark code, one line per code',
    'Ajouter le code': 'Add code', 'Indique un nom de code valide.': 'Enter a valid code name.',
    'La limite de 200 codes est atteinte.': 'The 200-code limit has been reached.',
    "Les changements s'appliquent au prochain lancement.": 'Changes apply on the next launch.',
}


def install_ps1_language(project):
    """Localize PhantomStation's PS1 additions using the launcher's language."""
    path = Path(project) / 'recomp-ui/src/common/backends/imgui/launcher_imgui.cpp'
    source = path.read_text(encoding='utf-8')
    if 'PHANTOM_INTERFACE_LANGUAGE_V1' in source:
        return
    anchor = 'static const char* ui_text(const char* en) {'
    if source.count(anchor) != 1:
        raise ValueError('Point de traduction du lanceur PS1 introuvable.')
    helper = '''// PHANTOM_INTERFACE_LANGUAGE_V1: the game text is not modified.
static const char* phantom_ui_language(const char* fr, const char* en) {
    const char* lang = std::getenv("PHANTOM_LANGUAGE");
    return lang && std::strcmp(lang, "en") == 0 ? en : fr;
}

'''
    source = source.replace(anchor, helper + anchor, 1)
    count = 0
    for french, english in PS1_ENGLISH.items():
        old = json.dumps(french, ensure_ascii=False)
        replacement = 'phantom_ui_language(' + old + ', ' + json.dumps(english, ensure_ascii=False) + ')'
        occurrences = source.count(old)
        source = source.replace(old, replacement)
        count += occurrences
    if count < 5:
        raise ValueError('Les textes du lanceur PS1 sont introuvables.')
    path.write_text(source, encoding='utf-8', newline='\n')


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
    language = '(std::getenv("PHANTOM_LANGUAGE") && !std::strcmp(std::getenv("PHANTOM_LANGUAGE"), "en") ? "Play" : "Jouer")'
    source = source.replace(main, 'neon_cta("##play", m->in_session ? ui_text("RESUME") : ' + language + ',', 1)
    source = source.replace(lobby, 'neon_cta("##lobby_play", ' + language + ',', 1)
    source = source.replace(active, '    bool act = ImGui::IsItemActive();\n'
        '    // PHANTOM_PLAY_PRESS_V1: a clear pressed state for mouse and gamepad.\n'
        '    bool phantom_pressed = enabled && act && (std::strcmp(id, "##play") == 0 || std::strcmp(id, "##lobby_play") == 0);\n'
        '    bool foc = ImGui::IsItemFocused();', 1)
    source = source.replace(gradient, '    grad_rect(dl, mn, mx, r, top, bot);\n'
        '    if (phantom_pressed) dl->AddRectFilled(mn, mx, IM_COL32(0, 12, 40, 105), r);\n'
        '    dl->AddRect(mn, mx, imcol(th.accent, hov ? 0.9f : 0.5f),', 1)
    source = source.replace(center, center + '\n    if (phantom_pressed) cy += px(2.0f);', 1)
    path.write_text(source, encoding='utf-8', newline='\n')
