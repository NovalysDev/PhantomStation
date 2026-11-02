"""Ajoute ZQSD/WASD aux commandes clavier du menu en jeu."""
from pathlib import Path


def install_menu_keyboard(project):
    root = Path(project)
    provider = root / 'recomp-ui/src/consoles/psx/psx_menu_provider_imgui.cpp'
    source = provider.read_text(encoding='utf-8')
    if 'PHANTOM_MENU_KEYBOARD_V1' not in source:
        replacements = {
            '    case SDLK_UP: return RECOMP_RUNTIME_UI_INPUT_UP;': '    case SDLK_UP: case \'z\': case \'w\': return RECOMP_RUNTIME_UI_INPUT_UP;',
            '    case SDLK_DOWN: return RECOMP_RUNTIME_UI_INPUT_DOWN;': '    case SDLK_DOWN: case \'s\': return RECOMP_RUNTIME_UI_INPUT_DOWN;',
            '    case SDLK_LEFT: return RECOMP_RUNTIME_UI_INPUT_LEFT;': '    case SDLK_LEFT: case \'q\': case \'a\': return RECOMP_RUNTIME_UI_INPUT_LEFT;',
            '    case SDLK_RIGHT: return RECOMP_RUNTIME_UI_INPUT_RIGHT;': '    case SDLK_RIGHT: case \'d\': return RECOMP_RUNTIME_UI_INPUT_RIGHT;',
        }
        for old, new in replacements.items():
            if source.count(old) != 1: raise ValueError('Navigation du menu introuvable.')
            source = source.replace(old, new, 1)
        provider.write_text('// PHANTOM_MENU_KEYBOARD_V1\n' + source, encoding='utf-8', newline='\n')
    main = root / 'psxrecomp/runtime/src/main.cpp'
    source = main.read_text(encoding='utf-8')
    if 'PHANTOM_MENU_KEYBOARD_V1' not in source:
        start = source.index('static void runtime_menu_host_loop(void) {')
        end = source.index('/* Freeze guest in vblank present while the rewind filmstrip is open. */', start)
        section = source[start:end]
        for old, new in {
            'key == SDLK_UP)': "key == SDLK_UP || key == 'z' || key == 'w')",
            'key == SDLK_DOWN)': "key == SDLK_DOWN || key == 's')",
            'key == SDLK_LEFT)': "key == SDLK_LEFT || key == 'q' || key == 'a')",
            'key == SDLK_RIGHT)': "key == SDLK_RIGHT || key == 'd')",
        }.items():
            if section.count(old) != 1: raise ValueError('Navigation du menu logiciel introuvable.')
            section = section.replace(old, new, 1)
        section = '// PHANTOM_MENU_KEYBOARD_V1\n' + section
        main.write_text(source[:start] + section + source[end:], encoding='utf-8', newline='\n')
