#pragma once
// Recherche en lecture seule dans la RAM PS1. Les résultats restent des pistes.
#include <algorithm>
#include <cstdint>
#include <vector>

namespace phantom_search {
struct Candidate { uint32_t address; uint8_t width; uint8_t changes; };
enum Compare { Decreased, Increased, Changed, Unchanged };

class Scanner {
    std::vector<uint8_t> base_, last_;
    std::vector<Candidate> candidates_;
    int category_ = -1;
    unsigned samples_ = 0, rounds_ = 0;
    static uint32_t read(const uint8_t* bytes, const Candidate& c) {
        uint32_t value = 0;
        for (unsigned i = 0; i < c.width; ++i) value |= uint32_t(bytes[c.address + i]) << (8 * i);
        return value;
    }
public:
    bool active() const { return category_ >= 0; }
    int category() const { return category_; }
    size_t count() const { return candidates_.size(); }
    unsigned samples() const { return samples_; }
    unsigned rounds() const { return rounds_; }
    const std::vector<Candidate>& candidates() const { return candidates_; }
    uint32_t value(const uint8_t* ram, const Candidate& c) const { return read(ram, c); }
    void reset() {
        category_ = -1;
        std::vector<uint8_t>().swap(base_); std::vector<uint8_t>().swap(last_);
        std::vector<Candidate>().swap(candidates_); samples_ = rounds_ = 0;
    }
    bool start(const uint8_t* ram, size_t bytes, int category) {
        if (!ram || bytes != 2u * 1024u * 1024u || category < 0 || category > 3) return false;
        reset(); category_ = category;
        base_.assign(ram, ram + bytes); last_ = base_;
        candidates_.reserve(bytes + bytes / 2 + bytes / 4);
        for (unsigned width : {1u, 2u, 4u})
            for (uint32_t a = 0; a + width <= bytes; a += width)
                candidates_.push_back({a, static_cast<uint8_t>(width), 0});
        return true;
    }
    void observe(const uint8_t* ram) {
        if (!active() || !ram) return;
        for (auto& c : candidates_)
            if (read(ram, c) != read(last_.data(), c) && c.changes < 255) ++c.changes;
        std::copy(ram, ram + last_.size(), last_.begin());
        ++samples_;
    }
    bool filter(const uint8_t* ram, Compare condition) {
        if (!active() || !ram) return false;
        size_t output = 0;
        for (const auto& c : candidates_) {
            uint32_t old = read(base_.data(), c), now = read(ram, c);
            const bool keep = condition == Decreased ? now < old : condition == Increased ? now > old :
                              condition == Changed ? now != old : now == old;
            if (keep) candidates_[output++] = c;
        }
        candidates_.resize(output);
        std::copy(ram, ram + base_.size(), base_.begin());
        std::copy(ram, ram + last_.size(), last_.begin());
        ++rounds_;
        return true;
    }
};
} // namespace phantom_search
