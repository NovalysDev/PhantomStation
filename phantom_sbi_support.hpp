// PhantomStation : SBI manquant et ajout au disque extrait dans son cache.
// Le catalogue fournit une indication par numéro de disque ; seul le registre
// disposant d'un SHA-256 peut certifier la correspondance exacte du fichier.
#pragma once
#include <algorithm>
#include <array>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <regex>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

static bool phantom_sbi_required(const std::string& serial) {
    static const char* required[] = { PHANTOM_SBI_REQUIRED_SERIALS };
    for (const auto* value : required)
        if (value && serial == value) return true;
    return false;
}

static bool phantom_sbi_valid(const std::filesystem::path& path) {
    std::error_code ec;
    const auto size = std::filesystem::file_size(path, ec);
    if (ec || size <= 4 || size > 65536 || (size - 4) % 14 != 0) return false;
    std::vector<unsigned char> data(static_cast<size_t>(size));
    std::ifstream input(path, std::ios::binary);
    if (!input.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(data.size()))) return false;
    if (data[0] != 'S' || data[1] != 'B' || data[2] != 'I' || data[3] != 0) return false;
    std::set<std::array<unsigned char, 3>> sectors;
    for (size_t i = 4; i < data.size(); i += 14) {
        if (data[i + 3] != 1) return false;
        for (size_t j = 0; j < 3; ++j)
            if ((data[i+j] & 15) > 9 || (data[i+j] >> 4) > 9) return false;
        if (!sectors.insert({data[i], data[i+1], data[i+2]}).second) return false;
    }
    return true;
}

static void phantom_sbi_install(const std::filesystem::path& cache,
                                const std::filesystem::path& mount,
                                const std::filesystem::path& data_track,
                                const std::filesystem::path& source,
                                const std::string& serial = {}) {
    namespace fs = std::filesystem;
    std::error_code ec;
    const fs::path media = fs::canonical(cache / "media", ec);
    if (ec) throw std::runtime_error("Dossier du disque du jeu introuvable.");
    const fs::path disc = fs::canonical(mount, ec);
    if (ec) throw std::runtime_error("Disque du jeu introuvable.");
    const fs::path relative = fs::relative(disc, media, ec);
    if (ec || relative.empty() || relative.is_absolute() || *relative.begin() == "..")
        throw std::runtime_error("Le SBI peut seulement etre ajoute au disque extrait de ce jeu.");
    if (!phantom_sbi_valid(source)) throw std::runtime_error("Fichier SBI invalide ou non pris en charge.");
    // When a serial is written in the filename, reject a different disc.
    std::string filename_id, expected_id;
    for (unsigned char c : source.stem().string()) if (std::isalnum(c)) filename_id += static_cast<char>(std::toupper(c));
    for (unsigned char c : serial) if (std::isalnum(c)) expected_id += static_cast<char>(std::toupper(c));
    std::smatch match;
    if (!expected_id.empty() && std::regex_search(filename_id, match, std::regex("(SLES|SCES|SLUS|SCUS|SLPS|SLPM|SCPS)[0-9]{5}")) && match.str() != expected_id)
        throw std::runtime_error("Le numero du SBI ne correspond pas a ce disque.");
    // Verify exact revisions when a known disc and SBI digest are available.
    const auto size = fs::file_size(data_track, ec);
    if (ec) throw std::runtime_error("Piste du disque introuvable.");
    std::string data_hash;
    for (const auto& rule : PSXRecompV4::kSbiRequirements) {
        if (size != rule.size) continue;
        if (data_hash.empty()) data_hash = PSXRecompV4::sbi_setup_hash(data_track);
        if (data_hash == rule.data_sha256 && PSXRecompV4::sbi_setup_hash(source) != rule.sbi_sha256)
            throw std::runtime_error("Ce SBI ne correspond pas a cette version du disque.");
    }
    fs::path destination = disc;
    destination.replace_extension(".sbi");
    if (fs::exists(destination, ec) && fs::equivalent(source, destination, ec)) return;
    fs::path temporary;
    for (unsigned n = 1; n < 10000; ++n) {
        fs::path candidate = destination.string() + ".phantom-ajout-" + std::to_string(n) + ".tmp";
        ec.clear();
        if (fs::copy_file(source, candidate, fs::copy_options::none, ec)) { temporary = candidate; break; }
        if (ec && ec != std::errc::file_exists) throw std::runtime_error("Impossible de copier le SBI dans le cache du jeu.");
    }
    if (temporary.empty()) throw std::runtime_error("Impossible de preparer le SBI.");
    fs::path previous;
    try {
        if (!phantom_sbi_valid(temporary) || PSXRecompV4::sbi_setup_hash(temporary) != PSXRecompV4::sbi_setup_hash(source))
            throw std::runtime_error("Le SBI a change pendant son ajout ; reessaye.");
        if (fs::exists(destination)) {
            for (unsigned n = 1; n < 10000; ++n) {
                fs::path candidate = destination.string() + ".precedent-" + std::to_string(n);
                if (!fs::exists(candidate)) { previous = candidate; break; }
            }
            if (previous.empty()) throw std::runtime_error("Impossible de conserver l'ancien SBI.");
            fs::rename(destination, previous);
        }
        try { fs::rename(temporary, destination); }
        catch (...) {
            if (!previous.empty()) { std::error_code ignored; fs::rename(previous, destination, ignored); }
            throw;
        }
    } catch (...) { fs::remove(temporary, ec); throw; }
}
