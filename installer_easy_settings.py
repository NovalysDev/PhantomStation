"""Expose les réglages graphiques PS1 dans la page Paramètres du jeu."""
from pathlib import Path

MARKER = 'PHANTOM_EASY_SETTINGS_V2'

HELPER = r'''
// PHANTOM_EASY_SETTINGS_V2: controls use the existing mod provider and its
// normal Play-time commit, so the original settings and validation still apply.
static bool phantom_find_feature(LauncherModel* m, const char* package,
                                 RecompLauncherCModFeature* out) {
    const auto* mods = m ? m->mods : nullptr;
    if (!mods || !mods->feature_count || !mods->feature_get) return false;
    for (int i = 0; i < mods->feature_count(mods->ctx); ++i) {
        RecompLauncherCModFeature f{};
        if (mods->feature_get(mods->ctx, i, &f) && !std::strcmp(f.package_id, package)) {
            *out = f;
            return true;
        }
    }
    return false;
}
static bool phantom_find_option(LauncherModel* m, const RecompLauncherCModFeature& f,
                                const char* id, RecompLauncherCModOption* out) {
    const auto* mods = m->mods;
    if (!mods->feature_option_get) return false;
    for (int i = 0; i < f.option_count; ++i) {
        RecompLauncherCModOption o{};
        if (mods->feature_option_get(mods->ctx, f.package_id, f.id, i, &o) && !std::strcmp(o.id, id)) {
            *out = o;
            return true;
        }
    }
    return false;
}
static void phantom_setting_error(LauncherModel* m) {
    const char* error = m->mods->last_error ? m->mods->last_error(m->mods->ctx) : nullptr;
    std::snprintf(m->mod_status, sizeof(m->mod_status), "%s", error && *error ? error : "Impossible d'enregistrer ce réglage.");
}
static void phantom_easy_settings(LauncherModel* m, const LauncherTheme& th) {
    const auto* mods = m ? m->mods : nullptr;
    if (!mods || !mods->feature_enable || !mods->feature_set_option) return;
    RecompLauncherCModFeature resolution{}, filtering{}, pgxp{};
    const bool has_resolution = phantom_find_feature(m, "psx.enhancement.internal-resolution", &resolution);
    const bool has_filtering = phantom_find_feature(m, "psx.enhancement.image-filtering", &filtering);
    const bool has_pgxp = phantom_find_feature(m, "psx.enhancement.pgxp", &pgxp);
    if (!has_resolution && !has_filtering && !has_pgxp) return;
    if (begin_panel("phantom-easy-settings", 0, false)) {
        eyebrow("QUALITE DE L'IMAGE");
        ImGui::TextWrapped("Choisis la netteté de l'image. Une valeur plus élevée demande plus de puissance.");
        RecompLauncherCModOption option{};
        if (has_resolution && phantom_find_option(m, resolution, "resolution", &option)) {
            int value = std::atoi(option.value);
            bool fixed = value >= 1 && value <= 8 && std::strchr(option.value, 'x');
            if (!fixed) value = 8;
            ImGui::TextUnformatted("Résolution interne du jeu");
            ImGui::SetNextItemWidth(ImGui::GetContentRegionAvail().x * .72f);
            if (ImGui::SliderInt("##phantom-resolution", &value, 1, 8, "%dx")) {
                char next[8]; std::snprintf(next, sizeof(next), "%dx", value);
                if ((!resolution.enabled && !mods->feature_enable(mods->ctx, resolution.package_id, resolution.id, 1)) ||
                    !mods->feature_set_option(mods->ctx, resolution.package_id, resolution.id, option.id, next))
                    phantom_setting_error(m);
            }
            if (!fixed) ImGui::TextDisabled("Réglage actuel : %s", option.value);
        }
        if (has_filtering && phantom_find_option(m, filtering, "smoothing", &option)) {
            bool smooth = filtering.enabled && !std::strcmp(option.value, "smooth");
            if (ImGui::Checkbox("Lisser l'image affichée", &smooth)) {
                if ((!filtering.enabled && !mods->feature_enable(mods->ctx, filtering.package_id, filtering.id, 1)) ||
                    !mods->feature_set_option(mods->ctx, filtering.package_id, filtering.id, option.id, smooth ? "smooth" : "sharp"))
                    phantom_setting_error(m);
            }
        }
        if (has_filtering && phantom_find_option(m, filtering, "texture_filtering", &option)) {
            int filter = !std::strcmp(option.value, "bilinear") ? 1 : !std::strcmp(option.value, "xbr") ? 2 : 0;
            const char* labels[] = {"Net", "Doux", "Contours adoucis"};
            ImGui::TextUnformatted("Lissage des textures 3D");
            ImGui::SetNextItemWidth(ImGui::GetContentRegionAvail().x * .72f);
            if (ImGui::SliderInt("##phantom-textures", &filter, 0, 2, labels[filter])) {
                const char* values[] = {"nearest", "bilinear", "xbr"};
                if ((!filtering.enabled && !mods->feature_enable(mods->ctx, filtering.package_id, filtering.id, 1)) ||
                    !mods->feature_set_option(mods->ctx, filtering.package_id, filtering.id, option.id, values[filter]))
                    phantom_setting_error(m);
            }
        }
        ImGui::Spacing();
        eyebrow("CORRECTION 3D");
        if (has_pgxp) {
            bool enabled = pgxp.enabled != 0;
            if (ImGui::Checkbox("Réduire les tremblements et déformations (PGXP)", &enabled)) {
                if (!mods->feature_enable(mods->ctx, pgxp.package_id, pgxp.id, enabled ? 1 : 0))
                    phantom_setting_error(m);
            }
            ImGui::TextWrapped("Si l'image présente des défauts, désactive cette correction.");
        }
        if (m->has_vsync) {
            ImGui::Spacing();
            eyebrow("AFFICHAGE");
            bool sync = m->s.vsync != RECOMP_LAUNCHER_VSYNC_OFF;
            const char* forced = launcher_model_vsync_forced_off_by(m);
            if (forced) ImGui::BeginDisabled();
            if (ImGui::Checkbox("Synchronisation verticale (VSync)", &sync))
                launcher_model_set_vsync(m, sync ? RECOMP_LAUNCHER_VSYNC_ON : RECOMP_LAUNCHER_VSYNC_OFF);
            if (forced) ImGui::EndDisabled();
        }
        if (m->mod_status[0]) ImGui::TextColored(col(th.warn), "%s", m->mod_status);
        ImGui::TextDisabled("Réglages enregistrés ; ils s'appliquent au prochain lancement.");
    }
    end_panel();
    ImGui::Spacing();
}
'''


def install_easy_settings(project):
    path = Path(project) / 'recomp-ui/src/common/backends/imgui/launcher_imgui.cpp'
    source = path.read_text(encoding='utf-8')
    if MARKER in source:
        return
    anchor = 'void draw_settings(LauncherModel* m, const LauncherTheme& th) {'
    if source.count(anchor) != 1:
        raise ValueError('La page de réglages a changé : installation impossible.')
    old = source.find('// PHANTOM_EASY_SETTINGS_V1:')
    if old >= 0:
        end = source.find(anchor, old)
        if end < 0:
            raise ValueError('Impossible de mettre à jour la page de réglages.')
        source = source[:old] + HELPER.strip() + '\n\n' + source[end:]
    else:
        source = source.replace(anchor, HELPER + '\n' + anchor + '\n    phantom_easy_settings(m, th);', 1)
    source = source.replace('ImGui::Button(ui_text("Settings"), ImVec2(w, px(34)))',
                            'ImGui::Button(ui_text("Réglages"), ImVec2(w, px(34)))', 1)
    source = source.replace('ui_text("< Back")', 'ui_text("< Retour")', 1)
    path.write_text(source, encoding='utf-8', newline='\n')
