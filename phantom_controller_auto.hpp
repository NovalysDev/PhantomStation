// Sélectionne une manette disponible pour le joueur 1 au premier branchement.
// Une sélection faite ensuite par le joueur reste libre jusqu'au prochain lancement.
#pragma once
#include <cstring>

static bool phantom_select_first_controller(LauncherModel* model, const LauncherPad* pads, int count) {
    if (!model || !pads || count <= 0) return false;
    if (model->s.player_src[0] == 2) {
        for (int i = 0; i < count; ++i) {
            const auto instance = model->s.player_gamepad_instance[0];
            const char* guid = model->s.player_gamepad_guid[0];
            if ((instance && instance == pads[i].id + 1) ||
                (!instance && guid[0] && pads[i].guid[0] && !std::strcmp(guid, pads[i].guid)))
                return true; // La manette enregistrée est déjà connectée.
        }
    }
    for (int i = 0; i < count; ++i) {
        bool reserved = false;
        for (int seat = 1; seat < LNG_MAX_PLAYERS; ++seat) {
            if (model->s.player_src[seat] != 2) continue;
            const auto instance = model->s.player_gamepad_instance[seat];
            const char* guid = model->s.player_gamepad_guid[seat];
            if ((instance && instance == pads[i].id + 1) ||
                (!instance && guid[0] && pads[i].guid[0] && !std::strcmp(guid, pads[i].guid))) {
                reserved = true;
                break;
            }
        }
        if (!reserved) {
            launcher_model_set_source(model, 0, 2, pads[i].id, pads[i].name, pads[i].guid);
            launcher_binds_refresh(model);
            return true;
        }
    }
    return true; // Toutes les manettes sont déjà attribuées à d'autres joueurs.
}
