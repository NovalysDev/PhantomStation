#pragma once
#include "phantom_cheat_search.hpp"
#include "phantom_codes.hpp"
#include <chrono>
#include <map>
#include <new>
#include <tuple>

extern "C" uint8_t* memory_get_ram_ptr(void);
static phantom_search::Scanner phantom_scanner;
static unsigned phantom_scanner_frames = 0;
static int phantom_scanner_disc = -1;
static size_t phantom_scanner_preview = 0;
static uint64_t phantom_search_session_id = static_cast<uint64_t>(
    std::chrono::high_resolution_clock::now().time_since_epoch().count());

static const char* phantom_scanner_category(int n) {
    switch (n) { case 0: return "Vie"; case 1: return "Munitions";
                 case 2: return "Carte"; case 3: return "Arme equipee"; default: return "Valeur"; }
}

// Aucune écriture dans la RAM : seule une liste de pistes désactivées est créée.
extern "C" int phantom_cheat_search_command(int command, char* message, int capacity) {
    if (!message || capacity <= 0) return 0;
    if (psx_netplay_active()) { std::snprintf(message, capacity, "Recherche indisponible en jeu en ligne."); return 0; }
    if (!fntrace_is_game_started()) { std::snprintf(message, capacity, "Attends que le jeu ait demarre."); return 0; }
    uint8_t* ram = memory_get_ram_ptr();
    if (!ram || memory_get_ram_bytes() < phantom_codes::ram_size) {
        std::snprintf(message, capacity, "Memoire du jeu indisponible."); return 0;
    }
    if (command >= 0 && command <= 3) {
        try {
            if (!phantom_scanner.start(ram, phantom_codes::ram_size, command)) {
                std::snprintf(message, capacity, "Impossible de demarrer la recherche."); return 0;
            }
        } catch (const std::bad_alloc&) {
            phantom_scanner.reset();
            std::snprintf(message, capacity, "Memoire insuffisante pour cette recherche."); return 0;
        }
        phantom_scanner_disc = cdrom_disc_selected(); phantom_scanner_frames = 0; phantom_scanner_preview = 0;
        std::snprintf(message, capacity, "%s : reprends le jeu, fais changer la valeur, puis ouvre F10.", phantom_scanner_category(command));
        return 1;
    }
    if (!phantom_scanner.active() || cdrom_disc_selected() != phantom_scanner_disc) {
        phantom_scanner.reset();
        std::snprintf(message, capacity, "Commence une nouvelle recherche pour ce disque."); return 0;
    }
    if (command >= 4 && command <= 7) {
        const auto comparison = command == 4 ? phantom_search::Decreased : command == 5 ? phantom_search::Increased :
                                command == 6 ? phantom_search::Changed : phantom_search::Unchanged;
        phantom_scanner.filter(ram, comparison);
        phantom_scanner_preview = 0;
        std::snprintf(message, capacity, "%zu pistes. Reprends le jeu et compare encore ; 10 maximum pour proposer.", phantom_scanner.count());
        return 1;
    }
    if (command != 8) { std::snprintf(message, capacity, "Commande inconnue."); return 0; }
    if (!phantom_scanner.rounds() || !phantom_scanner.count() || phantom_scanner.count() > 10) {
        std::snprintf(message, capacity, "%zu pistes : compare encore jusqu'a en avoir entre 1 et 10.", phantom_scanner.count());
        return 0;
    }
    std::vector<phantom_codes::Cheat> proposals;
    std::string error;
    if (std::filesystem::exists("phantom-codes-suggestions.tsv") &&
        !phantom_codes::load_file("phantom-codes-suggestions.tsv", proposals, error)) {
        std::snprintf(message, capacity, "Anciennes pistes illisibles : elles sont conservees."); return 0;
    }
    // Les observations du même emplacement restent liées au disque et au
    // type de recherche. Un nouveau lancement n'est compté qu'après une
    // nouvelle recherche, jamais sur la seule base d'une adresse identique.
    using ObservationKey = std::tuple<int, unsigned, uint32_t, int>;
    struct Observation { uint64_t session = 0; unsigned sessions = 0; };
    std::map<ObservationKey, Observation> observations;
    {
        std::ifstream old("phantom-codes-suggestions.tsv", std::ios::binary);
        std::string row;
        while (std::getline(old, row)) {
            if (row.compare(0, 5, "#OBS\t") != 0) continue;
            int category, disc; unsigned width, address, sessions; uint64_t session;
            std::istringstream input(row.substr(5));
            if (input >> category >> width >> address >> disc >> session >> sessions &&
                category >= 0 && category <= 3 && (width == 1 || width == 2 || width == 4) &&
                address < phantom_codes::ram_size && disc >= 0 && disc <= 99 && sessions <= 10000)
                observations[{category, width, address, disc}] = {session, sessions};
        }
    }
    for (const auto& candidate : phantom_scanner.candidates()) {
        const uint32_t value = phantom_scanner.value(ram, candidate);
        const uint32_t prefix = candidate.width == 1 ? 0x30000000u : candidate.width == 2 ? 0x80000000u : 0x90000000u;
        char title[180], code[40];
        const int disc = phantom_scanner_disc > 0 ? phantom_scanner_disc : 1;
        auto& observation = observations[{phantom_scanner.category(), candidate.width, candidate.address, disc}];
        if (observation.session != phantom_search_session_id) {
            observation.session = phantom_search_session_id;
            if (observation.sessions < 10000) ++observation.sessions;
        }
        std::snprintf(title, sizeof title, "Piste %s %u bits %06X (CD%d, %u sessions, %u variations observees, non verifiee)",
                      phantom_scanner_category(phantom_scanner.category()), unsigned(candidate.width * 8),
                      candidate.address, disc, observation.sessions, unsigned(candidate.changes));
        if (candidate.width == 4) std::snprintf(code, sizeof code, "%08X %08X", prefix | candidate.address, value);
        else std::snprintf(code, sizeof code, "%08X %04X", prefix | candidate.address, value);
        phantom_codes::Cheat candidate_code;
        candidate_code.name = title; candidate_code.lines.push_back(code); candidate_code.enabled = false;
        if (!phantom_codes::parse(candidate_code)) continue;
        bool exists = false;
        const std::string category_prefix = std::string("Piste ") + phantom_scanner_category(phantom_scanner.category()) + " ";
        const std::string disc_tag = std::string("(CD") + std::to_string(disc) + ",";
        for (auto& prior : proposals) if (prior.lines == candidate_code.lines &&
            prior.name.compare(0, category_prefix.size(), category_prefix) == 0 &&
            prior.name.find(disc_tag) != std::string::npos) { prior.name = candidate_code.name; exists = true; break; }
        if (!exists) {
            if (proposals.size() >= 200) {
                std::snprintf(message, capacity, "Limite de 200 pistes atteinte ; anciennes pistes conservees."); return 0;
            }
            proposals.push_back(std::move(candidate_code));
        }
    }
    std::string data = "# Pistes automatiques NON VERIFIEES ; aucune n'est activee.\n";
    for (const auto& candidate : proposals) {
        data += "0\t" + candidate.name + "\t";
        for (size_t i = 0; i < candidate.lines.size(); ++i) {
            if (i) data += "|";
            data += candidate.lines[i];
        }
        data += "\n";
    }
    for (const auto& item : observations) {
        const auto& k = item.first; const auto& observation = item.second;
        data += "#OBS\t" + std::to_string(std::get<0>(k)) + "\t" + std::to_string(std::get<1>(k)) + "\t" +
                std::to_string(std::get<2>(k)) + "\t" + std::to_string(std::get<3>(k)) + "\t" +
                std::to_string(observation.session) + "\t" + std::to_string(observation.sessions) + "\n";
    }
    if (!psx::atomic_write_file("phantom-codes-suggestions.tsv", data, &error)) {
        std::snprintf(message, capacity, "Impossible d'enregistrer les pistes."); return 0;
    }
    const auto& preview = phantom_scanner.candidates()[phantom_scanner_preview % phantom_scanner.count()];
    std::snprintf(message, capacity, "Piste %zu/%zu : %s %u bits %06X, %u variations (non verifiee).",
                  phantom_scanner_preview % phantom_scanner.count() + 1, phantom_scanner.count(),
                  phantom_scanner_category(phantom_scanner.category()), unsigned(preview.width * 8),
                  preview.address, unsigned(preview.changes));
    ++phantom_scanner_preview;
    return 1;
}

static void phantom_cheat_search_tick() {
    if (!phantom_scanner.active()) return;
    if (!fntrace_is_game_started() || psx_netplay_active() || cdrom_disc_selected() != phantom_scanner_disc) {
        phantom_scanner.reset(); return;
    }
    // Un échantillon toutes les 100 images : les variations ne prouvent pas
    // qu'une adresse restera valable après un redémarrage ou un changement de zone.
    if (++phantom_scanner_frames >= 100) {
        phantom_scanner_frames = 0;
        phantom_scanner.observe(memory_get_ram_ptr());
    }
}
