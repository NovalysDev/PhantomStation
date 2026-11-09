"""Use PhantomStation's Documents folder for default game saves."""
from pathlib import Path

MARKER = 'PHANTOM_DOCUMENT_SAVES_V1'
ANCHOR = '''    /* Apply writable-state isolation before game-options and launcher setup,'''
HOOK = r'''
    // PHANTOM_DOCUMENT_SAVES_V1: redirect only the original default location.
#ifdef _WIN32
    if (!cli_memcard_dir) {
        const wchar_t* phantom_save_folder = _wgetenv(L"PHANTOM_SAVES_DIR");
        if (phantom_save_folder && *phantom_save_folder) {
            const auto phantom_exe_dir = exe_dir_from_argv(argv[0]).lexically_normal();
            const auto phantom_old_saves = (phantom_exe_dir / "saves").lexically_normal();
            const auto phantom_old_dir = memcard_dir.empty() ? phantom_exe_dir :
                anchor_on_exe_dir(argv[0], memcard_dir).lexically_normal();
            if (phantom_old_dir == phantom_old_saves || phantom_old_dir == phantom_exe_dir) {
                if (!memcard1_path.empty() && memcard1_path.lexically_normal() == phantom_old_dir / "card1.mcd")
                    memcard1_path.clear();
                if (!memcard2_path.empty() && memcard2_path.lexically_normal() == phantom_old_dir / "card2.mcd")
                    memcard2_path.clear();
                memcard_dir = std::filesystem::path(phantom_save_folder);
            }
        }
    }
#endif

'''

CARD_MARKER = 'PHANTOM_MULTI_CARDS_V1'
CARD_EARLY_MARKER = 'PHANTOM_MULTI_CARDS_EARLY'
CARD_LATE_ANCHOR = '    std::filesystem::path resolved_bios =\n'
CARD_HOOK = r'''
    // PHANTOM_MULTI_CARDS_V1: choices from the PhantomStation library.
#ifdef _WIN32
    {
        const wchar_t* card1 = _wgetenv(L"PHANTOM_SELECTED_PS1_CARD1");
        const wchar_t* card2 = _wgetenv(L"PHANTOM_SELECTED_PS1_CARD2");
        if (card1 && *card1) { memcard1_path = std::filesystem::path(card1); memcard1_enabled = true; }
        if (card2 && *card2) { memcard2_path = std::filesystem::path(card2); memcard2_enabled = true; }
    }
#endif
'''


def install_save_directory(project):
    path = Path(project) / 'psxrecomp' / 'runtime' / 'src' / 'main.cpp'
    source = path.read_text(encoding='utf-8')
    if MARKER not in source:
        if source.count(ANCHOR) != 1:
            raise ValueError('Impossible de relier le dossier des sauvegardes à ce moteur.')
        source = source.replace(ANCHOR, HOOK + ANCHOR, 1)
    if CARD_MARKER not in source:
        if source.count(CARD_LATE_ANCHOR) != 1:
            raise ValueError('Impossible de relier les deux cartes PS1 à ce moteur.')
        source = source.replace(CARD_LATE_ANCHOR, CARD_HOOK + CARD_LATE_ANCHOR, 1)
    if CARD_EARLY_MARKER not in source:
        if source.count(ANCHOR) != 1:
            raise ValueError('Impossible de préparer les cartes dans le lanceur PS1.')
        source = source.replace(ANCHOR, CARD_HOOK.replace(CARD_MARKER, CARD_EARLY_MARKER) + ANCHOR, 1)
    path.write_text(source, encoding='utf-8', newline='\n')
