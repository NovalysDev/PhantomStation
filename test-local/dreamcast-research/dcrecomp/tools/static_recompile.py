#!/usr/bin/env python3
"""
SH-4 Static Recompiler - Translates SH-4 binary code to C source code.

Reads the 1ST_READ.BIN executable and produces C functions that perform
equivalent operations using the SH4CPU state structure. Each SH-4 function
is translated to a C function that operates on the CPU state.

This is the core of the static recompilation approach - instead of
interpreting or dynamically recompiling SH-4 instructions at runtime,
we translate them ahead-of-time to native C code that can be compiled
with an optimizing C compiler (gcc/clang/MSVC).
"""

import struct
import sys
import json
import os
from collections import defaultdict

LOAD_ADDR = 0x8C010000

def sext8(v):
    return v if v < 128 else v - 256

def sext12(v):
    return v if v < 2048 else v - 4096

class SH4Recompiler:
    """Translates SH-4 binary functions to C code."""

    def __init__(self, binary_data, base_addr=LOAD_ADDR, extra_entries=()):
        self.data = binary_data
        self.extra_entries = set(extra_entries)
        self.entry_hints = set()
        self.base = base_addr
        self.size = len(binary_data)
        self.functions = {}
        self.call_targets = set()
        self.labels = set()        # Branch targets within functions
        self.string_refs = {}      # PC-relative data references
        self.indirect_calls = []   # Addresses with indirect JSR/JMP

    def runtime_literals(self):
        """Literal-pool words the program may rewrite while it runs.

        A PC-relative load is folded to the value in the binary, which is
        wrong for a slot the game uses as a variable: Grandia II keeps its
        interrupt stack pointer in its handler's literal pool and fills it at
        start-up, so the folded 0 sent every interrupt to address zero. A slot
        whose address appears as a word somewhere in the binary is taken as
        such a variable and read at run time. Only with --runtime-literals.
        """
        cached = getattr(self, '_runtime_literals', None)
        if cached is not None: return cached
        found = set()
        if RUNTIME_LITERALS:
            lo, hi = self.base & 0x1FFFFFFF, (self.base & 0x1FFFFFFF) + self.size
            for off in range(0, self.size - 3, 4):
                v = struct.unpack_from('<I', self.data, off)[0]
                p = v & 0x1FFFFFFF
                if lo <= p < hi and not (p & 3) and v >> 29 in (4, 5):
                    found.add(p | 0x80000000)
        self._runtime_literals = found
        return found

    def read_u16(self, offset):
        return struct.unpack_from('<H', self.data, offset)[0]

    def read_u32(self, offset):
        if offset + 4 <= self.size:
            return struct.unpack_from('<I', self.data, offset)[0]
        return 0

    def addr_to_offset(self, addr):
        return addr - self.base

    def offset_to_addr(self, offset):
        return self.base + offset

    def find_functions(self):
        """Identify function boundaries using heuristic analysis."""
        call_targets = set()
        prologue_addrs = set()

        # Pass 1: Find BSR targets and function prologues
        for offset in range(0, self.size - 1, 2):
            opcode = self.read_u16(offset)
            pc = self.offset_to_addr(offset)

            # BSR - direct subroutine call
            if (opcode & 0xF000) == 0xB000:
                d12 = opcode & 0xFFF
                disp = sext12(d12) * 2
                target = pc + 4 + disp
                call_targets.add(target)

            # STS.L PR, @-R15 (function prologue)
            if opcode == 0x4F22:
                prologue_addrs.add(pc)

        self.call_targets = call_targets

        # Entry points no static pass can see: targets the game computes at
        # runtime. They are per title, so they come from the game repo's
        # --entries file, not from here (see docs/entries.md).
        forced_entries = {self.base + 0x100} | self.extra_entries

        # Also scan for mov.l @(disp,PC) + JMP @Rn patterns to find indirect targets
        for i in range(0, len(self.data) - 4, 2):
            pc = self.base + i
            op = self.data[i] | (self.data[i+1] << 8)
            # Look for JMP @Rn (0100nnnn00101011) or JSR @Rn (0100nnnn00001011)
            if (op & 0xF0FF) == 0x400B or (op & 0xF0FF) == 0x402B:
                rn = (op >> 8) & 0xF
                # Look back for mov.l @(disp,PC), Rn to find the target.
                # Nearest first: a run of consecutive load/call pairs is the
                # normal shape of an SDK init sequence, and scanning forwards
                # matched an earlier pair's load every time - which silently
                # dropped the last target in every such run.
                for j in range(i - 2, max(-2, i - 22), -2):
                    if j < 0:
                        break
                    prev = self.data[j] | (self.data[j+1] << 8)
                    if (prev & 0xF000) == 0xD000 and ((prev >> 8) & 0xF) == rn:
                        disp = prev & 0xFF
                        lit_addr = ((self.base + j) & 0xFFFFFFFC) + 4 + disp * 4
                        lit_off = lit_addr - self.base
                        if 0 <= lit_off < len(self.data) - 3:
                            target = (self.data[lit_off] | (self.data[lit_off+1] << 8) |
                                     (self.data[lit_off+2] << 16) | (self.data[lit_off+3] << 24))
                            # Convert physical to cached address
                            if 0x0C000000 <= target < 0x0D000000:
                                target = target | 0x80000000
                            if self.base <= target < self.base + self.size:
                                forced_entries.add(target)
                        break
        for addr in forced_entries:
            if self.base <= addr < self.base + self.size:
                print(f"  Forced entry point: 0x{addr:08X}")

        # Combine entry points
        all_entries = sorted(set([self.base]) | call_targets | prologue_addrs | forced_entries)

        # Filter to valid range
        all_entries = [a for a in all_entries if self.base <= a < self.base + self.size]

        # Build function map
        for i, entry in enumerate(all_entries):
            end = all_entries[i + 1] if i + 1 < len(all_entries) else self.base + self.size
            self.functions[entry] = {
                'addr': entry,
                'end': end,
                'size': end - entry,
                'has_prologue': entry in prologue_addrs,
                'is_call_target': entry in call_targets,
            }

        print(f"Found {len(self.functions)} functions")

        # Addresses reached by a direct jump. Used to keep the instruction
        # walk in step when a literal pool decodes as a branch.
        self.entry_hints = (self._mova_targets()
                            | self._external_branch_targets()
                            | self._jump_table_targets()
                            | self._inline_branch_table_targets())

        self._register_boundary_continuations()
        self._resolve_mid_entries()

    def _register_boundary_continuations(self):
        """Make continuations after cross-boundary delay slots callable."""
        for addr, info in self.functions.items():
            end = self.addr_to_offset(info['end'])
            if end < 2 or end + 2 > self.size:
                continue
            pc = self.offset_to_addr(end - 2)
            code, branch, target, delay = self.recompile_instruction(self.read_u16(end - 2), pc)
            if (branch and delay and ('bsr' in code or 'jsr' in code or 'bsrf' in code
                                      or 'bt/s' in code or 'bf/s' in code)):
                continuation = pc + 4
                if continuation < self.base + self.size:
                    self.entry_hints.add(continuation)

    def _resolve_mid_entries(self):
        """Find entry points that land inside an existing function.

        Indirect calls through function pointers stored in RAM (vtables,
        callback tables, jump tables) cannot be resolved by looking at the code
        around the JSR - the target is only a value at runtime. But the value
        usually comes from a pointer table that is in the binary already, so
        scanning the data for words that look like code addresses finds most of
        them.

        These are NOT added as function boundaries. Splitting a function at an
        arbitrary address breaks any loop that spans the split, because a
        backward branch across the new boundary stops being a local goto. They
        are recorded per containing function instead, and recompile_function
        emits a shared implementation with a goto dispatch at the top.

        False positives are cheap here: an address that is not a real
        instruction boundary is dropped below, and one that is costs a switch
        case and a one-line wrapper that nothing ever calls.
        """
        import bisect

        candidates = set()
        for off in range(0, self.size - 3, 4):
            v = (self.data[off] | (self.data[off + 1] << 8) |
                 (self.data[off + 2] << 16) | (self.data[off + 3] << 24))
            if 0x0C000000 <= v < 0x0D000000:
                v |= 0x80000000
            if self.base <= v < self.base + self.size and (v & 1) == 0:
                candidates.add(v)
        candidates |= self.entry_hints
        candidates -= set(self.functions)

        starts = sorted(self.functions)
        by_func = {}
        for addr in candidates:
            i = bisect.bisect_right(starts, addr) - 1
            if i < 0:
                continue
            owner = starts[i]
            if addr < self.functions[owner]['end']:
                by_func.setdefault(owner, []).append(addr)

        self.mid_entries = {}
        for owner, addrs in by_func.items():
            emittable = self._emittable_addrs(owner, self.functions[owner]['end'])
            keep = sorted(a for a in addrs if a in emittable)
            if keep:
                self.mid_entries[owner] = keep

        self.mid_entry_addrs = {a for v in self.mid_entries.values() for a in v}
        print(f"Found {len(self.mid_entry_addrs)} mid-function entry points "
              f"in {len(self.mid_entries)} functions "
              f"({len(candidates)} candidates scanned)")

    def _external_branch_targets(self):
        """Branch targets that leave the function they were emitted in.

        recompile_function turns a branch it cannot resolve locally into
        `func_<target>(cpu); return;`. If <target> is a label inside some other
        function rather than a function start, no such function exists and
        generate_stubs.py fills it with a silent no-op - so the branch returns
        without running the epilogue it was heading for, and every callee-saved
        register the function had pushed stays clobbered. That was 5642 stubs
        quietly corrupting registers.

        Treating these as mid-function entries makes the branch land where it
        was supposed to.
        """
        targets = set()
        for addr, info in self.functions.items():
            offset = self.addr_to_offset(addr)
            end = self.addr_to_offset(info['end'])
            if offset < 0 or end > self.size:
                continue
            while offset < end - 1:
                pc = self.offset_to_addr(offset)
                opcode = self.read_u16(offset)
                _, is_branch, target, has_delay = self.recompile_instruction(opcode, pc)
                if is_branch and isinstance(target, int):
                    if not (addr <= target < info['end']):
                        targets.add(target)
                offset += 4 if self._takes_delay_slot(offset, has_delay) else 2
        return targets

    def _takes_delay_slot(self, offset, has_delay):
        """Whether the word after a delay-slot branch really is its delay slot.

        False when that address is something the program jumps to directly - a
        known entry point cannot also be a delay slot, and treating it as one
        desynchronises the walk through a mis-decoded literal pool.
        """
        if not has_delay:
            return False
        return self.offset_to_addr(offset + 2) not in self.entry_hints

    def _jump_table_targets(self):
        """Targets of `braf`/`bsrf` fed from a mova'd displacement table.

        The GCC SH idiom is:

            shll   r0                  ; index *= 2
            mov    r0, r1
            mova   @(disp,PC), r0      ; r0 = table
            mov.w  @(r0,r1), r0        ; r0 = table[index]
            braf   r0                  ; PC = braf + 4 + r0

        so each entry is a forward displacement from just past the branch. The
        table has no length marker; it is followed immediately by code, so read
        entries while they still land on an even address a sane distance ahead
        and stop at the first one that does not.
        """
        WINDOW = 0x4000          # switch arms live close to the branch
        MAX_ENTRIES = 256
        targets = set()

        for offset in range(0, self.size - 1, 2):
            opcode = self.read_u16(offset)
            if (opcode & 0xF0FF) not in (0x0023, 0x0003):   # braf / bsrf
                continue
            reg = (opcode >> 8) & 0xF
            braf_pc = self.offset_to_addr(offset)

            # Walk back for the mova that loaded the table, and the load that
            # indexes it - the load width tells us the entry size.
            table = None
            entry_size = 2
            for back in range(2, 20, 2):
                if offset - back < 0:
                    break
                prev = self.read_u16(offset - back)
                if (prev & 0xFF00) == 0xC700:               # mova @(disp,PC),r0
                    pc = self.offset_to_addr(offset - back)
                    table = (((pc + 4) & 0xFFFFFFFC) + (prev & 0xFF) * 4)
                    break
                if (prev & 0xF00F) == 0x000C and ((prev >> 8) & 0xF) == reg:
                    entry_size = 1                          # mov.b @(R0,Rm),Rn
            if table is None:
                continue

            base = braf_pc + 4
            for i in range(MAX_ENTRIES):
                off = self.addr_to_offset(table) + i * entry_size
                if off < 0 or off + entry_size > self.size:
                    break
                if entry_size == 2:
                    disp = self.read_u16(off)
                else:
                    disp = self.data[off] * 2
                target = base + disp
                if (target & 1) or not (base <= target < base + WINDOW):
                    break
                if not (self.base <= target < self.base + self.size):
                    break
                targets.add(target)
        return targets

    def _inline_branch_table_targets(self):
        """Identify BRAF tables containing inline BRA/delay-slot pairs."""
        targets = set()
        for offset in range(0, self.size - 1, 2):
            if (self.read_u16(offset) & 0xF0FF) != 0x0023:
                continue
            base = offset + 4
            first = base
            while first + 3 < self.size and first < base + 12:
                if (self.read_u16(first) & 0xF000) == 0xA000:
                    break
                if self.read_u16(first) != 0x0009:
                    first = -1
                    break
                first += 4
            if first < 0 or first + 7 >= self.size:
                continue
            table = []
            pos = first
            while pos + 3 < self.size and len(table) < 64:
                if (self.read_u16(pos) & 0xF000) != 0xA000:
                    break
                table.append(self.offset_to_addr(pos))
                pos += 4
            if len(table) >= 2:
                targets.update(table)
                targets.update(self.offset_to_addr(p) for p in range(base, first, 4))
        return targets

    def _mova_targets(self):
        """Addresses produced by MOVA @(disp,PC),R0.

        SDK code reaches its cache and store-queue routines through a P1->P2
        trampoline: MOVA to get the address of the following code, OR in
        0xA0000000 to make it uncached, then JMP. The target is computed at
        runtime from a PC-relative displacement, so neither the literal-pool
        scan nor the branch scan sees it - the jump lands on an address with no
        entry point, gets skipped, and the routine's epilogue never runs. One
        of those leaked four bytes of stack per call.

        MOVA is also used to address data (jump tables, string literals). Those
        candidates are dropped by the emittable filter or, if they survive it,
        cost one switch case that nothing ever calls.
        """
        targets = set()
        for offset in range(0, self.size - 1, 2):
            opcode = self.read_u16(offset)
            if (opcode & 0xFF00) == 0xC700:      # MOVA @(disp,PC), R0
                pc = self.offset_to_addr(offset)
                targets.add((((pc + 4) & 0xFFFFFFFC) + (opcode & 0xFF) * 4))
        return targets

    def _emittable_addrs(self, start_addr, end_addr):
        """Addresses inside [start,end) that begin an emitted instruction.

        Walks the same way recompile_function does, so delay slots and the
        insides of literal pools are excluded.
        """
        out = set()
        offset = self.addr_to_offset(start_addr)
        end = self.addr_to_offset(end_addr)
        if offset < 0 or end > self.size:
            return out
        while offset < end - 1:
            out.add(self.offset_to_addr(offset))
            opcode = self.read_u16(offset)
            _, _, _, has_delay = self.recompile_instruction(opcode,
                                                           self.offset_to_addr(offset))
            offset += 4 if self._takes_delay_slot(offset, has_delay) else 2
        return out

    def _setjmp_like(self, addr):
        """A routine that saves its caller's context through r4 and returns 0:
        it stores r15 and PR there, makes no call or branch and never loads r15.
        Whatever the compiler or library, that is what setjmp has to do."""
        cache = self.__dict__.setdefault('_setjmp_cache', {})
        if addr not in cache:
            ok = sp = pr = zero = False
            off = self.addr_to_offset(addr)
            for k in range(96):
                o = off + 2 * k
                if not 0 <= o < self.size - 1:
                    break
                op = self.read_u16(o)
                n, m = (op >> 8) & 0xF, (op >> 4) & 0xF
                if op == 0x000B:
                    zero |= 0 <= o + 2 < self.size - 1 and self.read_u16(o + 2) == 0xE000
                    ok = sp and pr and zero
                    break
                if ((op & 0xF000) in (0xA000, 0xB000) or (op & 0xFF00) in (0x8900, 0x8B00, 0x8D00, 0x8F00)
                        or (op & 0xF0FF) in (0x400B, 0x402B, 0x0003, 0x0023) or op == 0x002B):
                    break
                if n == 15 and ((op & 0xF00F) in (0x6002, 0x6003, 0x6006, 0x000E) or (op & 0xF000) in (0x5000, 0xD000)):
                    break
                zero |= op == 0xE000
                sp |= n == 4 and m == 15 and ((op & 0xF00F) in (0x2002, 0x2006) or (op & 0xF000) == 0x1000)
                pr |= (op & 0xF0FF) == 0x002A or op == 0x4422
            cache[addr] = ok
        return cache[addr]

    def _jsr_literal_target(self, offset, rn):
        """The address a JSR @rn calls when rn was loaded from a literal just
        before and left alone since."""
        for j in range(offset - 2, max(-2, offset - 22), -2):
            if j < 0:
                break
            prev = self.read_u16(j)
            if (prev & 0xF000) == 0xD000 and ((prev >> 8) & 0xF) == rn:
                lit = (self.offset_to_addr(j) & ~3) + 4 + (prev & 0xFF) * 4
                lo = self.addr_to_offset(lit)
                if not 0 <= lo < self.size - 3 or lit in self.runtime_literals():
                    return None
                t = self.read_u32(lo)
                return t | 0x80000000 if 0x0C000000 <= t < 0x0D000000 else t
            if ((prev & 0xF000) in (0xA000, 0xB000) or (prev & 0xFF00) in (0x8900, 0x8B00, 0x8D00, 0x8F00)
                    or prev == 0x000B):
                return None
            if ((prev >> 8) & 0xF) == rn and not ((prev & 0xF000) == 0x1000 or (prev & 0xF00F) in (0x2000, 0x2001, 0x2002)):
                return None
        return None

    def call_statement(self, offset, opcode, target, call):
        """The C statement for a call. With --setjmp-sites a call to a
        setjmp-like routine becomes a native resume point: when the game later
        restores that context, the host jumps back here and the call is
        skipped, the guest registers already being what its longjmp loaded
        (pal-prototype/threads.c)."""
        if not SETJMP_SITES:
            return call
        if not isinstance(target, int):
            target = self._jsr_literal_target(offset, (opcode >> 8) & 0xF) if (opcode & 0xF0FF) == 0x400B else None
        if target is None or not self._setjmp_like(target):
            return call
        return f"if (!phantom_ctx_save(phantom_setjmp_slot(cpu))) {call} /* setjmp 0x{target:08X} */"

    def recompile_instruction(self, opcode, pc):
        result = self._recompile_instruction(opcode, pc)
        # With --sp-loads, an instruction that loads the stack pointer - rather
        # than moving it by a frame - is counted. That is what tells a context
        # switch (a kernel or longjmp loading another stack) from a function
        # that only left its stack unbalanced.
        if SP_LOADS and (opcode & 0x0F00) == 0x0F00 and (
                (opcode & 0xF00F) in (0x6002, 0x6003, 0x6006, 0x000E) or (opcode & 0xF000) in (0x5000, 0xD000)):
            if (opcode & 0xF00F) != 0x6003 or ((opcode >> 4) & 0xF) != 15:
                code, *rest = result
                result = (code + " sh4_sp_loads++;", *rest)
        return result

    def _recompile_instruction(self, opcode, pc):
        """
        Translate a single SH-4 instruction to C code.
        Returns (c_code, is_branch, branch_target, is_delay_slot_insn).
        """
        n = (opcode >> 8) & 0xF
        m = (opcode >> 4) & 0xF
        d = opcode & 0xF
        i = opcode & 0xFF
        d8 = opcode & 0xFF
        d12 = opcode & 0xFFF

        # ---- NOP ----
        if opcode == 0x0009:
            return "/* nop */", False, None, False

        # ---- RTS ----
        if opcode == 0x000B:
            return "/* rts - return after delay slot */", True, "rts", True

        # ---- RTE ----
        if opcode == 0x002B:
            if IRQ_ON_SR:
                # RTE can switch the register bank back. It does not take
                # what it unmasks there and then (see sh4_sr_restored): taken
                # inside the RTE, an interrupt replaced the SPC it was about to
                # return to, and at the end of a thread switch it ran on the
                # old thread's host stack.
                return ("/* rte */\n    { uint32_t old_sr = cpu->sr; cpu->pc = cpu->spc; cpu->sr = cpu->ssr; "
                        "sh4_sr_restored(cpu, old_sr); }"), True, "rte", True
            return "/* rte */\n    cpu->pc = cpu->spc; cpu->sr = cpu->ssr;", True, "rte", True

        # ---- CLRT ----
        if opcode == 0x0008:
            return "cpu->sr &= ~SR_T;", False, None, False

        # ---- SETT ----
        if opcode == 0x0018:
            return "cpu->sr |= SR_T;", False, None, False

        # ---- CLRMAC ----
        if opcode == 0x0028:
            return "cpu->mach = 0; cpu->macl = 0;", False, None, False

        # ---- MOV Rm, Rn ----
        if (opcode & 0xF00F) == 0x6003:
            return f"cpu->r[{n}] = cpu->r[{m}];", False, None, False

        # ---- MOV #imm, Rn ----
        if (opcode & 0xF000) == 0xE000:
            imm = sext8(i)
            if imm >= 0:
                return f"cpu->r[{n}] = 0x{imm:X}u;", False, None, False
            else:
                return f"cpu->r[{n}] = 0x{imm & 0xFFFFFFFF:08X}u; /* {imm} */", False, None, False

        # ---- MOV.L @(disp,PC), Rn ----
        if (opcode & 0xF000) == 0xD000:
            disp = d8 * 4
            addr = (pc & 0xFFFFFFFC) + 4 + disp
            # Read the literal from the binary
            off = self.addr_to_offset(addr)
            if 0 <= off < self.size - 3 and addr not in self.runtime_literals():
                val = self.read_u32(off)
                # A compiler loads 0 with MOV #0, never from a literal pool, so
                # a zero slot is one the program fills while it runs. Crazy
                # Taxi's boot stub is position-independent and patches its own
                # pool through MOVA, leaving no pointer to the slot for
                # runtime_literals() to find: folded, its final jump went to 0.
                if not (RUNTIME_LITERALS and val == 0):
                    return f"cpu->r[{n}] = 0x{val:08X}u; /* @0x{addr:08X} */", False, None, False
            return f"cpu->r[{n}] = sh4_read32(cpu, 0x{addr:08X}u{RELOC_TEXT if RELOCATABLE else ''}); /* PC-rel */", False, None, False

        # ---- MOV.W @(disp,PC), Rn ----
        if (opcode & 0xF000) == 0x9000:
            disp = d8 * 2
            addr = pc + 4 + disp
            off = self.addr_to_offset(addr)
            if 0 <= off < self.size - 1 and (addr & ~3) not in self.runtime_literals():
                val = struct.unpack_from('<H', self.data, off)[0]
                sval = val if val < 0x8000 else val - 0x10000
                return f"cpu->r[{n}] = (int32_t)(int16_t)0x{val:04X}u; /* {sval} @0x{addr:08X} */", False, None, False
            return f"cpu->r[{n}] = (int32_t)(int16_t)sh4_read16(cpu, 0x{addr:08X}u{RELOC_TEXT if RELOCATABLE else ''});", False, None, False

        # ---- MOV.L Rm, @-Rn ---- (0010nnnnmmmm0110)
        if (opcode & 0xF00F) == 0x2006:
            return f"cpu->r[{n}] -= 4; sh4_write32(cpu, cpu->r[{n}], cpu->r[{m}]);", False, None, False

        # ---- MOV.L @Rm, Rn ----
        if (opcode & 0xF00F) == 0x6002:
            return f"cpu->r[{n}] = sh4_read32(cpu, cpu->r[{m}]);", False, None, False

        # ---- MOV.L @Rm+, Rn ----
        if (opcode & 0xF00F) == 0x6006:
            # With n == m the loaded value wins: the SH-4 skips the increment.
            if n == m:
                return f"cpu->r[{n}] = sh4_read32(cpu, cpu->r[{m}]);", False, None, False
            return f"cpu->r[{n}] = sh4_read32(cpu, cpu->r[{m}]); cpu->r[{m}] += 4;", False, None, False

        # ---- MOV.L Rm, @(disp,Rn) ----
        if (opcode & 0xF000) == 0x1000:
            disp = d * 4
            return f"sh4_write32(cpu, cpu->r[{n}] + {disp}, cpu->r[{m}]);", False, None, False

        # ---- MOV.L @(disp,Rm), Rn ----
        if (opcode & 0xF000) == 0x5000:
            disp = d * 4
            return f"cpu->r[{n}] = sh4_read32(cpu, cpu->r[{m}] + {disp});", False, None, False

        # ---- MOV.B Rm, @Rn ----
        if (opcode & 0xF00F) == 0x2000:
            return f"sh4_write8(cpu, cpu->r[{n}], (uint8_t)cpu->r[{m}]);", False, None, False

        # ---- MOV.B @Rm, Rn (sign extend) ----
        if (opcode & 0xF00F) == 0x6000:
            return f"cpu->r[{n}] = (int32_t)(int8_t)sh4_read8(cpu, cpu->r[{m}]);", False, None, False

        # ---- MOV.W Rm, @Rn ----
        if (opcode & 0xF00F) == 0x2001:
            return f"sh4_write16(cpu, cpu->r[{n}], (uint16_t)cpu->r[{m}]);", False, None, False

        # ---- MOV.W @Rm, Rn (sign extend) ----
        if (opcode & 0xF00F) == 0x6001:
            return f"cpu->r[{n}] = (int32_t)(int16_t)sh4_read16(cpu, cpu->r[{m}]);", False, None, False

        # ---- MOV.B @Rm+, Rn ----
        if (opcode & 0xF00F) == 0x6004:
            if n == m:
                return f"cpu->r[{n}] = (int32_t)(int8_t)sh4_read8(cpu, cpu->r[{m}]); /* r{n}==r{m} */", False, None, False
            return f"cpu->r[{n}] = (int32_t)(int8_t)sh4_read8(cpu, cpu->r[{m}]); cpu->r[{m}] += 1;", False, None, False

        # ---- MOV.W @Rm+, Rn ----
        if (opcode & 0xF00F) == 0x6005:
            if n == m:
                return f"cpu->r[{n}] = (int32_t)(int16_t)sh4_read16(cpu, cpu->r[{m}]);", False, None, False
            return f"cpu->r[{n}] = (int32_t)(int16_t)sh4_read16(cpu, cpu->r[{m}]); cpu->r[{m}] += 2;", False, None, False

        # ---- MOV.B Rm, @-Rn ----
        if (opcode & 0xF00F) == 0x2004:
            return f"cpu->r[{n}] -= 1; sh4_write8(cpu, cpu->r[{n}], (uint8_t)cpu->r[{m}]);", False, None, False

        # ---- MOV.W Rm, @-Rn ----
        if (opcode & 0xF00F) == 0x2005:
            return f"cpu->r[{n}] -= 2; sh4_write16(cpu, cpu->r[{n}], (uint16_t)cpu->r[{m}]);", False, None, False

        # ---- MOV.B R0, @(disp,Rn) ----
        if (opcode & 0xFF00) == 0x8000:
            disp = opcode & 0xF
            rn = (opcode >> 4) & 0xF
            return f"sh4_write8(cpu, cpu->r[{rn}] + {disp}, (uint8_t)cpu->r[0]);", False, None, False

        # ---- MOV.W R0, @(disp,Rn) ----
        if (opcode & 0xFF00) == 0x8100:
            disp = (opcode & 0xF) * 2
            rn = (opcode >> 4) & 0xF
            return f"sh4_write16(cpu, cpu->r[{rn}] + {disp}, (uint16_t)cpu->r[0]);", False, None, False

        # ---- MOV.B @(disp,Rm), R0 ----
        if (opcode & 0xFF00) == 0x8400:
            disp = opcode & 0xF
            rm = (opcode >> 4) & 0xF
            return f"cpu->r[0] = (int32_t)(int8_t)sh4_read8(cpu, cpu->r[{rm}] + {disp});", False, None, False

        # ---- MOV.W @(disp,Rm), R0 ----
        if (opcode & 0xFF00) == 0x8500:
            disp = (opcode & 0xF) * 2
            rm = (opcode >> 4) & 0xF
            return f"cpu->r[0] = (int32_t)(int16_t)sh4_read16(cpu, cpu->r[{rm}] + {disp});", False, None, False

        # ---- MOV.B/W/L @(R0,Rm), Rn ----
        if (opcode & 0xF00F) == 0x000C:
            return f"cpu->r[{n}] = (int32_t)(int8_t)sh4_read8(cpu, cpu->r[0] + cpu->r[{m}]);", False, None, False
        if (opcode & 0xF00F) == 0x000D:
            return f"cpu->r[{n}] = (int32_t)(int16_t)sh4_read16(cpu, cpu->r[0] + cpu->r[{m}]);", False, None, False
        if (opcode & 0xF00F) == 0x000E:
            return f"cpu->r[{n}] = sh4_read32(cpu, cpu->r[0] + cpu->r[{m}]);", False, None, False

        # ---- MOV.B/W/L Rm, @(R0,Rn) ----
        if (opcode & 0xF00F) == 0x0004:
            return f"sh4_write8(cpu, cpu->r[0] + cpu->r[{n}], (uint8_t)cpu->r[{m}]);", False, None, False
        if (opcode & 0xF00F) == 0x0005:
            return f"sh4_write16(cpu, cpu->r[0] + cpu->r[{n}], (uint16_t)cpu->r[{m}]);", False, None, False
        if (opcode & 0xF00F) == 0x0006:
            return f"sh4_write32(cpu, cpu->r[0] + cpu->r[{n}], cpu->r[{m}]);", False, None, False

        # ---- MOV.L Rm, @Rn ---- (0010nnnnmmmm0010)
        if (opcode & 0xF00F) == 0x2002:
            return f"sh4_write32(cpu, cpu->r[{n}], cpu->r[{m}]);", False, None, False

        # ---- MOV.L @Rm, Rn ---- (0110nnnnmmmm0010) - already handled

        # ---- MOVA @(disp,PC), R0 ----
        if (opcode & 0xFF00) == 0xC700:
            disp = d8 * 4
            addr = (pc & 0xFFFFFFFC) + 4 + disp
            return f"cpu->r[0] = 0x{addr:08X}u{RELOC_TEXT if RELOCATABLE else ''}; /* mova */", False, None, False

        # ---- MOVT Rn ----
        if (opcode & 0xF0FF) == 0x0029:
            return f"cpu->r[{n}] = (cpu->sr & SR_T) ? 1 : 0;", False, None, False

        # ---- ADD Rm, Rn ----
        if (opcode & 0xF00F) == 0x300C:
            return f"cpu->r[{n}] += cpu->r[{m}];", False, None, False

        # ---- ADD #imm, Rn ----
        if (opcode & 0xF000) == 0x7000:
            imm = sext8(i)
            if imm >= 0:
                return f"cpu->r[{n}] += {imm};", False, None, False
            else:
                return f"cpu->r[{n}] += (int32_t){imm};", False, None, False

        # ---- SUB Rm, Rn ----
        if (opcode & 0xF00F) == 0x3008:
            return f"cpu->r[{n}] -= cpu->r[{m}];", False, None, False

        # ---- CMP/EQ Rm, Rn ----
        if (opcode & 0xF00F) == 0x3000:
            return f"sh4_set_t(cpu, cpu->r[{n}] == cpu->r[{m}]);", False, None, False

        # ---- CMP/EQ #imm, R0 ----
        if (opcode & 0xFF00) == 0x8800:
            imm = sext8(i)
            return f"sh4_set_t(cpu, (int32_t)cpu->r[0] == {imm});", False, None, False

        # ---- CMP/GT Rm, Rn ----
        if (opcode & 0xF00F) == 0x3007:
            return f"sh4_set_t(cpu, (int32_t)cpu->r[{n}] > (int32_t)cpu->r[{m}]);", False, None, False

        # ---- CMP/GE Rm, Rn ----
        if (opcode & 0xF00F) == 0x3003:
            return f"sh4_set_t(cpu, (int32_t)cpu->r[{n}] >= (int32_t)cpu->r[{m}]);", False, None, False

        # ---- CMP/HI Rm, Rn (unsigned >) ----
        if (opcode & 0xF00F) == 0x3006:
            return f"sh4_set_t(cpu, cpu->r[{n}] > cpu->r[{m}]);", False, None, False

        # ---- CMP/HS Rm, Rn (unsigned >=) ----
        if (opcode & 0xF00F) == 0x3002:
            return f"sh4_set_t(cpu, cpu->r[{n}] >= cpu->r[{m}]);", False, None, False

        # ---- CMP/PL Rn (> 0) ----
        if (opcode & 0xF0FF) == 0x4015:
            return f"sh4_set_t(cpu, (int32_t)cpu->r[{n}] > 0);", False, None, False

        # ---- CMP/PZ Rn (>= 0) ----
        if (opcode & 0xF0FF) == 0x4011:
            return f"sh4_set_t(cpu, (int32_t)cpu->r[{n}] >= 0);", False, None, False

        # ---- TST Rm, Rn ----
        if (opcode & 0xF00F) == 0x2008:
            return f"sh4_set_t(cpu, (cpu->r[{n}] & cpu->r[{m}]) == 0);", False, None, False

        # ---- TST #imm, R0 ----
        if (opcode & 0xFF00) == 0xC800:
            return f"sh4_set_t(cpu, (cpu->r[0] & 0x{i:02X}u) == 0);", False, None, False

        # ---- AND Rm, Rn ----
        if (opcode & 0xF00F) == 0x2009:
            return f"cpu->r[{n}] &= cpu->r[{m}];", False, None, False

        # ---- AND #imm, R0 ----
        if (opcode & 0xFF00) == 0xC900:
            return f"cpu->r[0] &= 0x{i:02X}u;", False, None, False

        # ---- OR Rm, Rn ----
        if (opcode & 0xF00F) == 0x200B:
            return f"cpu->r[{n}] |= cpu->r[{m}];", False, None, False

        # ---- OR #imm, R0 ----
        if (opcode & 0xFF00) == 0xCB00:
            return f"cpu->r[0] |= 0x{i:02X}u;", False, None, False

        # ---- XOR Rm, Rn ----
        if (opcode & 0xF00F) == 0x200A:
            return f"cpu->r[{n}] ^= cpu->r[{m}];", False, None, False

        # ---- NOT Rm, Rn ----
        if (opcode & 0xF00F) == 0x6007:
            return f"cpu->r[{n}] = ~cpu->r[{m}];", False, None, False

        # ---- NEG Rm, Rn ----
        if (opcode & 0xF00F) == 0x600B:
            return f"cpu->r[{n}] = (uint32_t)(-(int32_t)cpu->r[{m}]);", False, None, False

        # ---- Shift operations ----
        if (opcode & 0xF0FF) == 0x4000:  # SHLL
            return f"sh4_set_t(cpu, (cpu->r[{n}] >> 31) & 1); cpu->r[{n}] <<= 1;", False, None, False
        if (opcode & 0xF0FF) == 0x4001:  # SHLR
            return f"sh4_set_t(cpu, cpu->r[{n}] & 1); cpu->r[{n}] >>= 1;", False, None, False
        if (opcode & 0xF0FF) == 0x4008:  # SHLL2
            return f"cpu->r[{n}] <<= 2;", False, None, False
        if (opcode & 0xF0FF) == 0x4009:  # SHLR2
            return f"cpu->r[{n}] >>= 2;", False, None, False
        if (opcode & 0xF0FF) == 0x4018:  # SHLL8
            return f"cpu->r[{n}] <<= 8;", False, None, False
        if (opcode & 0xF0FF) == 0x4019:  # SHLR8
            return f"cpu->r[{n}] >>= 8;", False, None, False
        if (opcode & 0xF0FF) == 0x4028:  # SHLL16
            return f"cpu->r[{n}] <<= 16;", False, None, False
        if (opcode & 0xF0FF) == 0x4029:  # SHLR16
            return f"cpu->r[{n}] >>= 16;", False, None, False
        if (opcode & 0xF0FF) == 0x4020:  # SHAL
            return f"sh4_set_t(cpu, (cpu->r[{n}] >> 31) & 1); cpu->r[{n}] <<= 1;", False, None, False
        if (opcode & 0xF0FF) == 0x4021:  # SHAR
            return f"sh4_set_t(cpu, cpu->r[{n}] & 1); cpu->r[{n}] = (uint32_t)((int32_t)cpu->r[{n}] >> 1);", False, None, False

        # ---- SHAD Rm, Rn ----
        if (opcode & 0xF00F) == 0x400C:
            return (f"{{\n"
                    f"        int32_t shift = (int32_t)cpu->r[{m}];\n"
                    f"        if (shift >= 0) cpu->r[{n}] <<= (shift & 31);\n"
                    f"        else if (shift > -32) cpu->r[{n}] = (uint32_t)((int32_t)cpu->r[{n}] >> (-shift));\n"
                    f"        else cpu->r[{n}] = ((int32_t)cpu->r[{n}] < 0) ? 0xFFFFFFFFu : 0;\n"
                    f"    }}"), False, None, False

        # ---- SHLD Rm, Rn ----
        if (opcode & 0xF00F) == 0x400D:
            return (f"{{\n"
                    f"        int32_t shift = (int32_t)cpu->r[{m}];\n"
                    f"        if (shift >= 0) cpu->r[{n}] <<= (shift & 31);\n"
                    f"        else if (shift > -32) cpu->r[{n}] >>= (-shift);\n"
                    f"        else cpu->r[{n}] = 0;\n"
                    f"    }}"), False, None, False

        # ---- EXTS/EXTU ----
        if (opcode & 0xF00F) == 0x600E:  # EXTS.B
            return f"cpu->r[{n}] = (uint32_t)(int32_t)(int8_t)(uint8_t)cpu->r[{m}];", False, None, False
        if (opcode & 0xF00F) == 0x600F:  # EXTS.W
            return f"cpu->r[{n}] = (uint32_t)(int32_t)(int16_t)(uint16_t)cpu->r[{m}];", False, None, False
        if (opcode & 0xF00F) == 0x600C:  # EXTU.B
            return f"cpu->r[{n}] = cpu->r[{m}] & 0xFF;", False, None, False
        if (opcode & 0xF00F) == 0x600D:  # EXTU.W
            return f"cpu->r[{n}] = cpu->r[{m}] & 0xFFFF;", False, None, False

        # ---- SWAP ----
        if (opcode & 0xF00F) == 0x6008:  # SWAP.B
            return f"cpu->r[{n}] = (cpu->r[{m}] & 0xFFFF0000u) | ((cpu->r[{m}] & 0xFF) << 8) | ((cpu->r[{m}] >> 8) & 0xFF);", False, None, False
        if (opcode & 0xF00F) == 0x6009:  # SWAP.W
            return f"cpu->r[{n}] = (cpu->r[{m}] << 16) | (cpu->r[{m}] >> 16);", False, None, False

        # ---- Multiply ----
        if (opcode & 0xF00F) == 0x200E:  # MULU.W
            return f"cpu->macl = (uint32_t)(uint16_t)cpu->r[{n}] * (uint32_t)(uint16_t)cpu->r[{m}];", False, None, False
        if (opcode & 0xF00F) == 0x200F:  # MULS.W
            return f"cpu->macl = (uint32_t)((int32_t)(int16_t)cpu->r[{n}] * (int32_t)(int16_t)cpu->r[{m}]);", False, None, False
        if (opcode & 0xF00F) == 0x0007:  # MUL.L
            return f"cpu->macl = cpu->r[{n}] * cpu->r[{m}];", False, None, False
        if (opcode & 0xF00F) == 0x3005:  # DMULU.L
            return (f"{{ uint64_t result = (uint64_t)cpu->r[{n}] * (uint64_t)cpu->r[{m}];\n"
                    f"    cpu->macl = (uint32_t)result; cpu->mach = (uint32_t)(result >> 32); }}")  , False, None, False
        if (opcode & 0xF00F) == 0x300D:  # DMULS.L
            return (f"{{ int64_t result = (int64_t)(int32_t)cpu->r[{n}] * (int64_t)(int32_t)cpu->r[{m}];\n"
                    f"    cpu->macl = (uint32_t)result; cpu->mach = (uint32_t)(result >> 32); }}"), False, None, False

        # ---- STS/LDS (system registers) ----
        if (opcode & 0xF0FF) == 0x001A:  # STS MACL, Rn
            return f"cpu->r[{n}] = cpu->macl;", False, None, False
        if (opcode & 0xF0FF) == 0x000A:  # STS MACH, Rn
            return f"cpu->r[{n}] = cpu->mach;", False, None, False
        if (opcode & 0xF0FF) == 0x002A:  # STS PR, Rn
            return f"cpu->r[{n}] = cpu->pr;", False, None, False
        if (opcode & 0xF0FF) == 0x005A:  # STS FPUL, Rn
            return f"cpu->r[{n}] = cpu->fpul;", False, None, False
        if (opcode & 0xF0FF) == 0x006A:  # STS FPSCR, Rn
            return f"cpu->r[{n}] = cpu->fpscr;", False, None, False

        if (opcode & 0xF0FF) == 0x402A:  # LDS Rm, PR
            return f"cpu->pr = cpu->r[{n}];", False, None, False
        if (opcode & 0xF0FF) == 0x401A:  # LDS Rm, MACL
            return f"cpu->macl = cpu->r[{n}];", False, None, False
        if (opcode & 0xF0FF) == 0x400A:  # LDS Rm, MACH
            return f"cpu->mach = cpu->r[{n}];", False, None, False
        if (opcode & 0xF0FF) == 0x405A:  # LDS Rm, FPUL
            return f"cpu->fpul = cpu->r[{n}];", False, None, False
        if (opcode & 0xF0FF) == 0x406A:  # LDS Rm, FPSCR
            return f"cpu->fpscr = cpu->r[{n}];", False, None, False

        # ---- STS.L/LDS.L (push/pop system registers) ----
        if (opcode & 0xF0FF) == 0x4022:  # STS.L PR, @-Rn
            return f"cpu->r[{n}] -= 4; sh4_write32(cpu, cpu->r[{n}], cpu->pr);", False, None, False
        if (opcode & 0xF0FF) == 0x4026:  # LDS.L @Rm+, PR
            return f"cpu->pr = sh4_read32(cpu, cpu->r[{n}]); cpu->r[{n}] += 4;", False, None, False
        if (opcode & 0xF0FF) == 0x4012:  # STS.L MACL, @-Rn
            return f"cpu->r[{n}] -= 4; sh4_write32(cpu, cpu->r[{n}], cpu->macl);", False, None, False
        if (opcode & 0xF0FF) == 0x4016:  # LDS.L @Rm+, MACL
            return f"cpu->macl = sh4_read32(cpu, cpu->r[{n}]); cpu->r[{n}] += 4;", False, None, False
        if (opcode & 0xF0FF) == 0x4002:  # STS.L MACH, @-Rn
            return f"cpu->r[{n}] -= 4; sh4_write32(cpu, cpu->r[{n}], cpu->mach);", False, None, False
        if (opcode & 0xF0FF) == 0x4006:  # LDS.L @Rm+, MACH
            return f"cpu->mach = sh4_read32(cpu, cpu->r[{n}]); cpu->r[{n}] += 4;", False, None, False
        if (opcode & 0xF0FF) == 0x4052:  # STS.L FPUL, @-Rn
            return f"cpu->r[{n}] -= 4; sh4_write32(cpu, cpu->r[{n}], cpu->fpul);", False, None, False
        if (opcode & 0xF0FF) == 0x4056:  # LDS.L @Rm+, FPUL
            return f"cpu->fpul = sh4_read32(cpu, cpu->r[{n}]); cpu->r[{n}] += 4;", False, None, False
        if (opcode & 0xF0FF) == 0x4062:  # STS.L FPSCR, @-Rn
            return f"cpu->r[{n}] -= 4; sh4_write32(cpu, cpu->r[{n}], cpu->fpscr);", False, None, False
        if (opcode & 0xF0FF) == 0x4066:  # LDS.L @Rm+, FPSCR
            return f"cpu->fpscr = sh4_read32(cpu, cpu->r[{n}]); cpu->r[{n}] += 4;", False, None, False

        # ---- STC/LDC (control registers) ----
        if (opcode & 0xF0FF) == 0x0002:  # STC SR, Rn
            return f"cpu->r[{n}] = cpu->sr;", False, None, False
        if (opcode & 0xF0FF) == 0x0012:  # STC GBR, Rn
            return f"cpu->r[{n}] = cpu->gbr;", False, None, False
        if (opcode & 0xF0FF) == 0x0022:  # STC VBR, Rn
            return f"cpu->r[{n}] = cpu->vbr;", False, None, False
        if (opcode & 0xF0FF) == 0x400E:  # LDC Rm, SR
            if IRQ_ON_SR:
                # An interrupt that became deliverable is taken here, as on the
                # SH-4: a critical section that only unmasks for a few
                # instructions has no memory access for the usual poll to see.
                return (f"{{ uint32_t old_sr = cpu->sr; cpu->sr = cpu->r[{n}]; "
                        f"sh4_sr_written(cpu, old_sr); }}"), False, None, False
            return f"cpu->sr = cpu->r[{n}];", False, None, False
        if (opcode & 0xF0FF) == 0x401E:  # LDC Rm, GBR
            return f"cpu->gbr = cpu->r[{n}];", False, None, False
        if (opcode & 0xF0FF) == 0x402E:  # LDC Rm, VBR
            return f"cpu->vbr = cpu->r[{n}];", False, None, False

        # The rest of the privileged control registers. Thread switches and
        # hand-built exception frames use them; left out, a STC.L that should
        # push became a no-op and every call drifted the stack by four bytes
        # until saved registers were popped as zeros (Grandia II).
        ctl = {0x3: 'ssr', 0x4: 'spc'}
        sub = (opcode >> 4) & 0xF
        if (opcode & 0xF0FF) == 0x00FA:  # STC DBR, Rn
            return f"cpu->r[{n}] = cpu->dbr;", False, None, False
        if (opcode & 0xF0FF) == 0x40FA:  # LDC Rm, DBR
            return f"cpu->dbr = cpu->r[{n}];", False, None, False
        if (opcode & 0xF00F) == 0x0002 and sub in ctl:  # STC SSR/SPC, Rn
            return f"cpu->r[{n}] = cpu->{ctl[sub]};", False, None, False
        if (opcode & 0xF0FF) == 0x003A:  # STC SGR, Rn
            return f"cpu->r[{n}] = cpu->sgr;", False, None, False
        if (opcode & 0xF08F) == 0x0082:  # STC Rm_BANK, Rn
            return f"cpu->r[{n}] = cpu->r_bank[{sub & 7}];", False, None, False
        if (opcode & 0xF00F) == 0x400E and sub in ctl:  # LDC Rm, SSR/SPC
            return f"cpu->{ctl[sub]} = cpu->r[{n}];", False, None, False
        if (opcode & 0xF08F) == 0x408E:  # LDC Rm, Rn_BANK
            return f"cpu->r_bank[{sub & 7}] = cpu->r[{n}];", False, None, False
        stl = {0x0: 'sr', 0x1: 'gbr', 0x2: 'vbr', 0x3: 'ssr', 0x4: 'spc'}
        if (opcode & 0xF00F) == 0x4003 and sub in stl:  # STC.L SR/GBR/VBR/SSR/SPC, @-Rn
            return (f"cpu->r[{n}] -= 4; sh4_write32(cpu, cpu->r[{n}], cpu->{stl[sub]});",
                    False, None, False)
        if (opcode & 0xF0FF) == 0x4032:  # STC.L SGR, @-Rn
            return f"cpu->r[{n}] -= 4; sh4_write32(cpu, cpu->r[{n}], cpu->sgr);", False, None, False
        if (opcode & 0xF0FF) == 0x40F2:  # STC.L DBR, @-Rn
            return f"cpu->r[{n}] -= 4; sh4_write32(cpu, cpu->r[{n}], cpu->dbr);", False, None, False
        if (opcode & 0xF08F) == 0x4083:  # STC.L Rm_BANK, @-Rn
            return (f"cpu->r[{n}] -= 4; sh4_write32(cpu, cpu->r[{n}], cpu->r_bank[{sub & 7}]);",
                    False, None, False)
        if (opcode & 0xF0FF) == 0x4007:  # LDC.L @Rm+, SR
            if IRQ_ON_SR:
                return (f"{{ uint32_t old_sr = cpu->sr; cpu->sr = sh4_read32(cpu, cpu->r[{n}]); "
                        f"cpu->r[{n}] += 4; sh4_sr_written(cpu, old_sr); }}"), False, None, False
            return f"cpu->sr = sh4_read32(cpu, cpu->r[{n}]); cpu->r[{n}] += 4;", False, None, False
        if (opcode & 0xF00F) == 0x4007 and sub in stl:  # LDC.L @Rm+, GBR/VBR/SSR/SPC
            return (f"cpu->{stl[sub]} = sh4_read32(cpu, cpu->r[{n}]); cpu->r[{n}] += 4;",
                    False, None, False)
        if (opcode & 0xF0FF) == 0x40F6:  # LDC.L @Rm+, DBR
            return f"cpu->dbr = sh4_read32(cpu, cpu->r[{n}]); cpu->r[{n}] += 4;", False, None, False
        if (opcode & 0xF08F) == 0x4087:  # LDC.L @Rm+, Rn_BANK
            return (f"cpu->r_bank[{sub & 7}] = sh4_read32(cpu, cpu->r[{n}]); cpu->r[{n}] += 4;",
                    False, None, False)
        if (opcode & 0xF0FF) == 0x00C3:  # MOVCA.L R0, @Rn
            return f"sh4_write32(cpu, cpu->r[{n}], cpu->r[0]);", False, None, False

        # ---- DT Rn (decrement and test) ----
        if (opcode & 0xF0FF) == 0x4010:
            return f"cpu->r[{n}]--; sh4_set_t(cpu, cpu->r[{n}] == 0);", False, None, False

        # ---- Branch instructions ----

        # BRA
        if (opcode & 0xF000) == 0xA000:
            disp = sext12(d12) * 2
            target = pc + 4 + disp
            return f"/* bra 0x{target:08X} */", True, target, True

        # BSR
        if (opcode & 0xF000) == 0xB000:
            disp = sext12(d12) * 2
            target = pc + 4 + disp
            return f"cpu->pr = 0x{pc + 4:08X}u; /* bsr 0x{target:08X} */", True, target, True

        # BT
        if (opcode & 0xFF00) == 0x8900:
            disp = sext8(d8) * 2
            target = pc + 4 + disp
            return f"/* bt 0x{target:08X} */", True, target, False

        # BF
        if (opcode & 0xFF00) == 0x8B00:
            disp = sext8(d8) * 2
            target = pc + 4 + disp
            return f"/* bf 0x{target:08X} */", True, target, False

        # BT/S
        if (opcode & 0xFF00) == 0x8D00:
            disp = sext8(d8) * 2
            target = pc + 4 + disp
            return f"/* bt/s 0x{target:08X} */", True, target, True

        # BF/S
        if (opcode & 0xFF00) == 0x8F00:
            disp = sext8(d8) * 2
            target = pc + 4 + disp
            return f"/* bf/s 0x{target:08X} */", True, target, True

        # JMP @Rm
        if (opcode & 0xF0FF) == 0x402B:
            return f"cpu->pc = cpu->r[{n}]; /* jmp @r{n} */", True, "indirect", True

        # JSR @Rm
        if (opcode & 0xF0FF) == 0x400B:
            return f"cpu->pr = 0x{pc + 4:08X}u; cpu->pc = cpu->r[{n}]; /* jsr @r{n} */", True, "indirect", True

        # BRAF Rm
        if (opcode & 0xF0FF) == 0x0023:
            return f"cpu->pc = 0x{pc + 4:08X}u + cpu->r[{n}]; /* braf r{n} */", True, "indirect", True

        # BSRF Rm
        if (opcode & 0xF0FF) == 0x0003:
            return f"cpu->pr = 0x{pc + 4:08X}u; cpu->pc = 0x{pc + 4:08X}u + cpu->r[{n}]; /* bsrf r{n} */", True, "indirect", True

        # ---- Floating point ----

        if (opcode & 0xF00F) == 0xF00C:  # FMOV
            return f"sh4_fmov_reg(cpu, {n}, {m});", False, None, False
        if (opcode & 0xF00F) == 0xF008:  # FMOV.S @Rm, FRn
            return f"sh4_fmov_load(cpu, {n}, cpu->r[{m}]);", False, None, False
        if (opcode & 0xF00F) == 0xF00A:  # FMOV.S FRm, @Rn
            return f"sh4_fmov_store(cpu, {m}, cpu->r[{n}]);", False, None, False
        if (opcode & 0xF00F) == 0xF009:  # FMOV.S @Rm+, FRn
            return f"sh4_fmov_load_inc(cpu, {n}, {m});", False, None, False
        if (opcode & 0xF00F) == 0xF00B:  # FMOV.S FRm, @-Rn
            return f"sh4_fmov_store_dec(cpu, {m}, {n});", False, None, False
        if (opcode & 0xF00F) == 0xF006:  # FMOV.S @(R0,Rm), FRn
            return f"sh4_fmov_load(cpu, {n}, cpu->r[0] + cpu->r[{m}]);", False, None, False
        if (opcode & 0xF00F) == 0xF007:  # FMOV.S FRm, @(R0,Rn)
            return f"sh4_fmov_store(cpu, {m}, cpu->r[0] + cpu->r[{n}]);", False, None, False

        if (opcode & 0xF00F) == 0xF000:  # FADD
            return f"cpu->fr[{n}] += cpu->fr[{m}];", False, None, False
        if (opcode & 0xF00F) == 0xF001:  # FSUB
            return f"cpu->fr[{n}] -= cpu->fr[{m}];", False, None, False
        if (opcode & 0xF00F) == 0xF002:  # FMUL
            return f"cpu->fr[{n}] *= cpu->fr[{m}];", False, None, False
        if (opcode & 0xF00F) == 0xF003:  # FDIV
            return f"cpu->fr[{n}] /= cpu->fr[{m}];", False, None, False
        if (opcode & 0xF00F) == 0xF004:  # FCMP/EQ
            return f"sh4_set_t(cpu, cpu->fr[{n}] == cpu->fr[{m}]);", False, None, False
        if (opcode & 0xF00F) == 0xF005:  # FCMP/GT
            return f"sh4_set_t(cpu, cpu->fr[{n}] > cpu->fr[{m}]);", False, None, False

        if (opcode & 0xF0FF) == 0xF02D:  # FLOAT FPUL, FRn
            return (f"{{ union {{ uint32_t u; float f; }} conv; conv.u = cpu->fpul;\n"
                    f"    cpu->fr[{n}] = (float)(int32_t)cpu->fpul; }}"), False, None, False
        if (opcode & 0xF0FF) == 0xF03D:  # FTRC FRm, FPUL
            return f"cpu->fpul = (uint32_t)(int32_t)cpu->fr[{n}]; /* ftrc */", False, None, False
        if (opcode & 0xF0FF) == 0xF04D:  # FNEG
            return f"cpu->fr[{n}] = -cpu->fr[{n}];", False, None, False
        if (opcode & 0xF0FF) == 0xF05D:  # FABS
            return f"cpu->fr[{n}] = fabsf(cpu->fr[{n}]);", False, None, False
        if (opcode & 0xF0FF) == 0xF06D:  # FSQRT
            return f"cpu->fr[{n}] = sqrtf(cpu->fr[{n}]);", False, None, False
        if (opcode & 0xF0FF) == 0xF01D:  # FLDS FRm, FPUL
            return f"{{ union {{ float f; uint32_t u; }} c; c.f = cpu->fr[{n}]; cpu->fpul = c.u; }}", False, None, False
        if (opcode & 0xF0FF) == 0xF00D:  # FSTS FPUL, FRn
            return f"{{ union {{ uint32_t u; float f; }} c; c.u = cpu->fpul; cpu->fr[{n}] = c.f; }}", False, None, False

        # FLDI0/FLDI1 - the constants every bit of graphics code is built from
        if (opcode & 0xF0FF) == 0xF08D:  # FLDI0 FRn
            return f"cpu->fr[{n}] = 0.0f;", False, None, False
        if (opcode & 0xF0FF) == 0xF09D:  # FLDI1 FRn
            return f"cpu->fr[{n}] = 1.0f;", False, None, False

        # FSRRA - reciprocal square root
        if (opcode & 0xF0FF) == 0xF07D:
            return (f"cpu->fr[{n}] = (cpu->fr[{n}] > 0.0f) "
                    f"? 1.0f / sqrtf(cpu->fr[{n}]) : cpu->fr[{n}];"), False, None, False

        # FRCHG and FSCHG share the 0xFnFD shape with FSCA and FTRV, so they
        # have to be recognised first.
        if opcode == 0xFBFD:
            return "sh4_frchg(cpu);", False, None, False
        if opcode == 0xF3FD:
            return "cpu->fpscr ^= FPSCR_SZ; /* fschg */", False, None, False

        # FSCA FPUL, DRn - sine and cosine of an angle, 65536 to the turn
        if (opcode & 0xF1FF) == 0xF0FD:
            base = n & 0xE
            return (f"{{ float a = (float)(cpu->fpul & 0xFFFF) "
                    f"* (2.0f * 3.14159265358979323846f / 65536.0f);\n"
                    f"    cpu->fr[{base}] = sinf(a); cpu->fr[{base + 1}] = cosf(a); }} /* fsca */"), False, None, False

        # FTRV XMTRX, FVn
        if (opcode & 0xF3FF) == 0xF1FD:
            b = n & 0xC
            rows = []
            for i in range(4):
                terms = " + ".join(
                    f"cpu->xf[{i + 4 * j}] * v[{j}]" for j in range(4))
                rows.append(f"    cpu->fr[{b + i}] = {terms};")
            body = "\n".join(rows)
            return ("{ float v[4] = { cpu->fr[%d], cpu->fr[%d], cpu->fr[%d], cpu->fr[%d] };\n"
                    "%s } /* ftrv */" % (b, b + 1, b + 2, b + 3, body)), False, None, False

        # FIPR (dot product)
        if (opcode & 0xF0FF) == 0xF0ED:
            vn = (n >> 2) & 3
            vm = n & 3
            vn_base = vn * 4
            vm_base = vm * 4
            return (f"cpu->fr[{vn_base + 3}] = "
                    f"cpu->fr[{vm_base}]*cpu->fr[{vn_base}] + "
                    f"cpu->fr[{vm_base+1}]*cpu->fr[{vn_base+1}] + "
                    f"cpu->fr[{vm_base+2}]*cpu->fr[{vn_base+2}] + "
                    f"cpu->fr[{vm_base+3}]*cpu->fr[{vn_base+3}]; /* fipr */"), False, None, False

        # FMAC FR0, FRm, FRn
        if (opcode & 0xF00F) == 0xF00E:
            return f"cpu->fr[{n}] += cpu->fr[0] * cpu->fr[{m}];", False, None, False

        # ---- Division ----
        # The SH-4 divides by stepping: DIV0S or DIV0U to set up, then one DIV1
        # per result bit. Without these the compiler's __udivsi3 hands back the
        # numerator and every division in the binary is quietly wrong.
        if opcode == 0x0019:  # DIV0U
            return ("cpu->sr &= ~(SR_Q | SR_M | SR_T); /* div0u */",
                    False, None, False)
        if (opcode & 0xF00F) == 0x2007:  # DIV0S Rm,Rn
            return (f"{{ unsigned q = (cpu->r[{n}] >> 31) & 1, mm = (cpu->r[{m}] >> 31) & 1;\n"
                    f"    cpu->sr = (cpu->sr & ~(SR_Q | SR_M | SR_T))\n"
                    f"              | (q ? SR_Q : 0) | (mm ? SR_M : 0)\n"
                    f"              | ((q ^ mm) ? SR_T : 0); }} /* div0s */",
                    False, None, False)
        if (opcode & 0xF00F) == 0x3004:  # DIV1 Rm,Rn
            return f"sh4_div1(cpu, {n}, {m});", False, None, False

        # ---- SR flag bits ----
        if opcode == 0x0048:  # CLRS
            return "cpu->sr &= ~SR_S;", False, None, False
        if opcode == 0x0058:  # SETS
            return "cpu->sr |= SR_S;", False, None, False
        if opcode == 0x001B:  # SLEEP
            return "/* sleep: nothing to wait for here */", False, None, False

        # ---- TAS.B ----
        if (opcode & 0xF0FF) == 0x401B:
            return (f"{{ uint8_t v = sh4_read8(cpu, cpu->r[{n}]);\n"
                    f"    sh4_set_t(cpu, v == 0);\n"
                    f"    sh4_write8(cpu, cpu->r[{n}], (uint8_t)(v | 0x80)); }} /* tas.b */",
                    False, None, False)

        # ---- ADDC/SUBC ----
        if (opcode & 0xF00F) == 0x300E:  # ADDC
            return (f"{{ uint32_t tmp = cpu->r[{n}]; uint32_t t = sh4_get_t(cpu) ? 1 : 0;\n"
                    f"    cpu->r[{n}] += cpu->r[{m}] + t;\n"
                    f"    sh4_set_t(cpu, (cpu->r[{n}] < tmp) || (t && cpu->r[{n}] == tmp)); }}"), False, None, False
        if (opcode & 0xF00F) == 0x300A:  # SUBC
            return (f"{{ uint32_t tmp = cpu->r[{n}]; uint32_t t = sh4_get_t(cpu) ? 1 : 0;\n"
                    f"    cpu->r[{n}] -= cpu->r[{m}] + t;\n"
                    f"    sh4_set_t(cpu, (tmp < cpu->r[{n}]) || (t && tmp == cpu->r[{n}])); }}"), False, None, False

        # ---- ROTL/ROTR ----
        if (opcode & 0xF0FF) == 0x4004:  # ROTL
            return f"sh4_set_t(cpu, (cpu->r[{n}] >> 31) & 1); cpu->r[{n}] = (cpu->r[{n}] << 1) | ((cpu->r[{n}] >> 31) & 1);", False, None, False
        if (opcode & 0xF0FF) == 0x4005:  # ROTR
            return f"sh4_set_t(cpu, cpu->r[{n}] & 1); cpu->r[{n}] = (cpu->r[{n}] >> 1) | ((cpu->r[{n}] & 1) << 31);", False, None, False
        if (opcode & 0xF0FF) == 0x4024:  # ROTCL
            return (f"{{ uint32_t old_t = sh4_get_t(cpu) ? 1 : 0;\n"
                    f"    sh4_set_t(cpu, (cpu->r[{n}] >> 31) & 1);\n"
                    f"    cpu->r[{n}] = (cpu->r[{n}] << 1) | old_t; }}"), False, None, False
        if (opcode & 0xF0FF) == 0x4025:  # ROTCR
            return (f"{{ uint32_t old_t = sh4_get_t(cpu) ? 1 : 0;\n"
                    f"    sh4_set_t(cpu, cpu->r[{n}] & 1);\n"
                    f"    cpu->r[{n}] = (cpu->r[{n}] >> 1) | (old_t << 31); }}"), False, None, False

        # ---- PREF/OCBI/OCBP/OCBWB (cache ops - mostly NOPs in recompilation) ----
        if (opcode & 0xF0FF) == 0x0083:  # PREF
            return f"sh4_sq_prefetch(cpu, cpu->r[{n}]);", False, None, False
        if (opcode & 0xF0FF) == 0x0093:  # OCBI
            return f"/* ocbi @r{n} (nop) */", False, None, False
        if (opcode & 0xF0FF) == 0x00A3:  # OCBP
            return f"/* ocbp @r{n} (nop) */", False, None, False
        if (opcode & 0xF0FF) == 0x00B3:  # OCBWB
            return f"/* ocbwb @r{n} (nop) */", False, None, False

        # ---- XTRCT ----
        if (opcode & 0xF00F) == 0x200D:
            return f"cpu->r[{n}] = (cpu->r[{n}] >> 16) | (cpu->r[{m}] << 16);", False, None, False

        # ---- NEGC ----
        if (opcode & 0xF00F) == 0x600A:
            return (f"{{ uint32_t t = sh4_get_t(cpu) ? 1 : 0;\n"
                    f"    cpu->r[{n}] = (uint32_t)(-(int32_t)cpu->r[{m}]) - t;\n"
                    f"    sh4_set_t(cpu, cpu->r[{m}] != 0 || t != 0); }}"), False, None, False

        # ---- CMP/STR ----
        if (opcode & 0xF00F) == 0x200C:
            return (f"{{ uint32_t tmp = cpu->r[{n}] ^ cpu->r[{m}];\n"
                    f"    sh4_set_t(cpu, !(tmp & 0xFF) || !((tmp>>8)&0xFF) || !((tmp>>16)&0xFF) || !((tmp>>24)&0xFF)); }}"), False, None, False

        # ---- MAC.L @Rm+, @Rn+ ----
        if (opcode & 0xF00F) == 0x000F:
            return (f"{{ int64_t mac = ((int64_t)cpu->mach << 32) | cpu->macl;\n"
                    f"    int32_t a = (int32_t)sh4_read32(cpu, cpu->r[{n}]);\n"
                    f"    int32_t b = (int32_t)sh4_read32(cpu, cpu->r[{m}]);\n"
                    f"    mac += (int64_t)a * (int64_t)b;\n"
                    f"    cpu->mach = (uint32_t)(mac >> 32); cpu->macl = (uint32_t)mac;\n"
                    f"    cpu->r[{n}] += 4; cpu->r[{m}] += 4; }}"), False, None, False

        # ---- TRAPA ----
        if (opcode & 0xFF00) == 0xC300:
            return f"/* trapa #0x{i:02X} - system call */", True, "trap", False

        # ---- GBR-relative MOV ----
        if (opcode & 0xFF00) == 0xC000:
            return f"sh4_write8(cpu, cpu->gbr + {d8}, (uint8_t)cpu->r[0]);", False, None, False
        if (opcode & 0xFF00) == 0xC100:
            return f"sh4_write16(cpu, cpu->gbr + {d8*2}, (uint16_t)cpu->r[0]);", False, None, False
        if (opcode & 0xFF00) == 0xC200:
            return f"sh4_write32(cpu, cpu->gbr + {d8*4}, cpu->r[0]);", False, None, False
        if (opcode & 0xFF00) == 0xC400:
            return f"cpu->r[0] = (int32_t)(int8_t)sh4_read8(cpu, cpu->gbr + {d8});", False, None, False
        if (opcode & 0xFF00) == 0xC500:
            return f"cpu->r[0] = (int32_t)(int16_t)sh4_read16(cpu, cpu->gbr + {d8*2});", False, None, False
        if (opcode & 0xFF00) == 0xC600:
            return f"cpu->r[0] = sh4_read32(cpu, cpu->gbr + {d8*4});", False, None, False

        # ---- ADDV/SUBV ----
        if (opcode & 0xF00F) == 0x300F:  # ADDV
            return (f"{{ int32_t a = (int32_t)cpu->r[{n}], b = (int32_t)cpu->r[{m}];\n"
                    f"    int32_t result = a + b;\n"
                    f"    sh4_set_t(cpu, ((a ^ result) & (b ^ result)) < 0);\n"
                    f"    cpu->r[{n}] = (uint32_t)result; }}"), False, None, False
        if (opcode & 0xF00F) == 0x300B:  # SUBV
            return (f"{{ int32_t a = (int32_t)cpu->r[{n}], b = (int32_t)cpu->r[{m}];\n"
                    f"    int32_t result = a - b;\n"
                    f"    sh4_set_t(cpu, ((a ^ b) & (a ^ result)) < 0);\n"
                    f"    cpu->r[{n}] = (uint32_t)result; }}"), False, None, False

        # ---- MAC.W ----
        if (opcode & 0xF00F) == 0x400F:
            return (f"{{ int32_t a = (int32_t)(int16_t)sh4_read16(cpu, cpu->r[{n}]);\n"
                    f"    int32_t b = (int32_t)(int16_t)sh4_read16(cpu, cpu->r[{m}]);\n"
                    f"    int64_t mac = ((int64_t)cpu->mach << 32) | cpu->macl;\n"
                    f"    mac += (int64_t)a * (int64_t)b;\n"
                    f"    cpu->mach = (uint32_t)(mac >> 32); cpu->macl = (uint32_t)mac;\n"
                    f"    cpu->r[{n}] += 2; cpu->r[{m}] += 2; }}"), False, None, False

        # ---- XOR #imm, R0 ----
        if (opcode & 0xFF00) == 0xCA00:
            return f"cpu->r[0] ^= 0x{i:02X}u;", False, None, False

        # ---- Default: unknown instruction ----
        return f"/* UNKNOWN: 0x{opcode:04X} at 0x{pc:08X} */", False, None, False

    def recompile_function(self, func_addr, func_info, next_func_addr=None):
        """Recompile a single function to C code."""
        func_start = self.addr_to_offset(func_addr)
        func_end = self.addr_to_offset(func_info['end'])

        if func_start < 0 or func_end > self.size:
            return None

        # First pass: collect branch targets and emittable addresses
        branch_targets = set()
        emittable_addrs = set()
        offset = func_start
        while offset < func_end - 1:
            pc = self.offset_to_addr(offset)
            emittable_addrs.add(pc)
            opcode = self.read_u16(offset)
            _, is_branch, target, has_delay = self.recompile_instruction(opcode, pc)
            if is_branch and isinstance(target, int):
                if func_addr <= target < func_info['end']:
                    branch_targets.add(target)
            if self._takes_delay_slot(offset, has_delay):
                # Delay slot instruction is also emittable
                if offset + 2 < func_end:
                    emittable_addrs.add(self.offset_to_addr(offset + 2))
                offset += 4
            else:
                offset += 2

        # Only label addresses that are both branch targets AND emittable
        local_labels = branch_targets & emittable_addrs

        # Generate function name
        func_name = f"func_{func_addr:08X}"

        # Always include function's own start address as a local label
        # (needed for self-tail-call → goto conversion to avoid stack overflow)
        local_labels.add(func_addr)

        # Mid-function entry points (see _resolve_mid_entries). Rather than
        # splitting the function - which would turn backward branches across the
        # split into calls that re-enter from the top - the body is emitted once
        # and each entry gets a wrapper that jumps straight to its label.
        mids = [a for a in getattr(self, 'mid_entries', {}).get(func_addr, ())
                if a in emittable_addrs and a != func_addr]
        local_labels |= set(mids)

        # Second pass: generate C code
        lines = []
        if mids:
            lines.append(f"static void impl_{func_addr:08X}(SH4CPU *cpu, uint32_t entry) {{")
            lines.append("    switch (entry) {")
            for a in mids:
                lines.append(f"    case 0x{a:08X}u: goto L_{a:08X};")
            lines.append("    default: break;")
            lines.append("    }")
        else:
            lines.append(f"void {func_name}(SH4CPU *cpu) {{")

        # Track which labels we've actually emitted
        emitted_labels = set()
        # When a delay slot is also an entry point, the branch path executes an
        # inline copy and jumps over the separately emitted entry instruction.
        pending_after_slots = {}

        offset = func_start
        while offset < func_end - 1:
            opcode = self.read_u16(offset)
            pc = self.offset_to_addr(offset)

            for branch_pc in pending_after_slots.pop(pc, ()):
                lines.append(f"L_AFTER_SLOT_{branch_pc:08X}:;")

            # Insert label if this is a branch target
            if pc in local_labels:
                lines.append(f"L_{pc:08X}:;")
                emitted_labels.add(pc)

            code, is_branch, target, has_delay = self.recompile_instruction(opcode, pc)

            # A target is "local" only if it has a label AND we can emit it
            is_local = isinstance(target, int) and target in local_labels
            is_external_func = isinstance(target, int) and not is_local

            # Handle non-delay-slot branches first (BT, BF without /S)
            if is_branch and not has_delay and isinstance(target, int):
                if is_local:
                    if "bt" in code:
                        lines.append(f"    if (cpu->sr & SR_T) goto L_{target:08X}; /* bt */")
                    elif "bf" in code:
                        lines.append(f"    if (!(cpu->sr & SR_T)) goto L_{target:08X}; /* bf */")
                else:
                    # Branch target outside function - treat as conditional tail call
                    if "bt" in code:
                        lines.append(f"    if (cpu->sr & SR_T) {{ func_{target:08X}(cpu); return; }} /* bt tail */")
                    elif "bf" in code:
                        lines.append(f"    if (!(cpu->sr & SR_T)) {{ func_{target:08X}(cpu); return; }} /* bf tail */")
                offset += 2
                continue

            # Branch delay slots retain their semantics across a discovered
            # function boundary. Calling the next function from its beginning
            # would execute the slot twice, while falling through used to skip
            # the branch altogether. Continue at pc+4 through its own wrapper.
            if (is_branch and has_delay and offset + 2 >= func_end
                    and offset + 2 < self.size):
                delay_pc = self.offset_to_addr(offset + 2)
                delay_code, _, _, _ = self.recompile_instruction(
                    self.read_u16(offset + 2), delay_pc)
                continuation = pc + 4
                continue_call = (f"func_{continuation:08X}(cpu); "
                                 if continuation < self.base + self.size else "")
                if target == 'rts':
                    lines.append(f"    {delay_code} /* delay slot, across boundary */")
                    lines.append("    return; /* rts */")
                elif target == 'rte':
                    lines.append(f"    {code}")
                    lines.append(f"    {delay_code} /* delay slot, across boundary */")
                    lines.append("    return; /* rte */")
                elif target == 'indirect':
                    lines.append(f"    {code}")
                    lines.append(f"    {delay_code} /* delay slot, across boundary */")
                    if 'jsr' in code or 'bsrf' in code:
                        lines.append("    " + self.call_statement(offset, opcode, None, "sh4_call_indirect(cpu);"))
                        lines.append(f"    {continue_call}return;")
                    else:
                        lines.append("    sh4_jump_indirect(cpu); return;")
                elif isinstance(target, int) and 'bsr' in code:
                    lines.append(f"    {code}")
                    lines.append(f"    {delay_code} /* delay slot, across boundary */")
                    lines.append("    " + self.call_statement(offset, opcode, target, f"func_{target:08X}(cpu);"))
                    lines.append(f"    {continue_call}return;")
                elif isinstance(target, int) and ('bt/s' in code or 'bf/s' in code):
                    cond = 't' if 'bt/s' in code else '!t'
                    branch_action = (f"goto L_{target:08X};" if is_local else
                                     f"{{ func_{target:08X}(cpu); return; }}")
                    lines.append("    { int t = cpu->sr & SR_T;")
                    lines.append(f"    {delay_code} /* delay slot, across boundary */")
                    lines.append(f"    if ({cond}) {branch_action} }}")
                    lines.append(f"    {continue_call}return;")
                elif isinstance(target, int) and 'bra' in code:
                    lines.append(f"    {delay_code} /* delay slot, across boundary */")
                    lines.append(f"    goto L_{target:08X};" if is_local else
                                 f"    func_{target:08X}(cpu); return;")
                else:
                    lines.append(f"    {code}")
                    lines.append(f"    {delay_code} /* delay slot, across boundary */")
                    lines.append("    return;")
                offset += 4
                continue

            # Handle delay slots for branch instructions
            if has_delay and offset + 2 < func_end:
                delay_opcode = self.read_u16(offset + 2)
                delay_pc = self.offset_to_addr(offset + 2)
                delay_code, _, _, _ = self.recompile_instruction(delay_opcode, delay_pc)

                # The slot can also be somewhere the program jumps to directly.
                # It is still this branch's delay slot - it just cannot be
                # *consumed* here, or the entry point loses its label and the
                # code it starts is never emitted. Inline a copy of the slot to
                # finish the branch, then let the walk re-emit it under its
                # label. Skipping the branch instead drops the `return` off an
                # rts and falls through into the next state on the caller's
                # frame, which is how a scene that should run once loops.
                slot_is_entry = (delay_pc in local_labels or delay_pc in pending_after_slots
                                 or not self._takes_delay_slot(offset, has_delay))
                returns_to_next = slot_is_entry and (
                    "bsr" in code or "bt/s" in code or "bf/s" in code
                    or "jsr" in code or "bsrf" in code)

                # Emit label at delay-slot position if needed
                if delay_pc in local_labels and not slot_is_entry:
                    lines.append(f"L_{delay_pc:08X}:;")
                    emitted_labels.add(delay_pc)

                if target == "rts":
                    lines.append(f"    {delay_code} /* delay slot */")
                    lines.append(f"    return; /* rts */")
                elif target == "rte":
                    # RTE ends the routine like RTS. Without the return the
                    # literal pool after it ran as code: Grandia II's thread
                    # switch went on to write into RAM from its constants.
                    lines.append(f"    {code}")
                    lines.append(f"    {delay_code} /* delay slot */")
                    lines.append(f"    return; /* rte */")
                elif isinstance(target, int) and "bsr" in code:
                    # BSR - a real call, so control returns here afterwards.
                    # Classify by the instruction, never by whether the target
                    # happens to be BSR'd from somewhere else in the binary: a
                    # BRA to a shared helper is a tail jump, and emitting it as
                    # a call makes execution fall through into whatever the
                    # assembler happened to place next.
                    lines.append(f"    {code}")
                    lines.append(f"    {delay_code} /* delay slot */")
                    lines.append("    " + self.call_statement(offset, opcode, target, f"func_{target:08X}(cpu); /* bsr */"))
                elif is_local:
                    # Local branch (BRA, BT/S, BF/S within function)
                    if "bt/s" in code:
                        lines.append(f"    {{ int t = cpu->sr & SR_T;")
                        lines.append(f"    {delay_code} /* delay slot */")
                        lines.append(f"    if (t) goto L_{target:08X}; }}")
                    elif "bf/s" in code:
                        lines.append(f"    {{ int t = cpu->sr & SR_T;")
                        lines.append(f"    {delay_code} /* delay slot */")
                        lines.append(f"    if (!t) goto L_{target:08X}; }}")
                    else:
                        # BRA local
                        lines.append(f"    {delay_code} /* delay slot */")
                        lines.append(f"    goto L_{target:08X};")
                elif is_external_func:
                    # External branch/call
                    if "bt/s" in code:
                        lines.append(f"    {{ int t = cpu->sr & SR_T;")
                        lines.append(f"    {delay_code} /* delay slot */")
                        lines.append(f"    if (t) {{ func_{target:08X}(cpu); return; }} }} /* bt/s tail */")
                    elif "bf/s" in code:
                        lines.append(f"    {{ int t = cpu->sr & SR_T;")
                        lines.append(f"    {delay_code} /* delay slot */")
                        lines.append(f"    if (!t) {{ func_{target:08X}(cpu); return; }} }} /* bf/s tail */")
                    elif "bsr" in code or "bra" not in code:
                        # BSR external
                        lines.append(f"    {delay_code} /* delay slot */")
                        lines.append("    " + self.call_statement(offset, opcode, target, f"func_{target:08X}(cpu);"))
                    else:
                        # BRA external (tail call)
                        lines.append(f"    {delay_code} /* delay slot */")
                        lines.append(f"    func_{target:08X}(cpu); return; /* bra tail */")
                elif target == "indirect":
                    # JSR/JMP @Rn
                    if "jsr" in code or "bsrf" in code:
                        lines.append(f"    {code}")
                        lines.append(f"    {delay_code} /* delay slot */")
                        lines.append("    " + self.call_statement(offset, opcode, None, "sh4_call_indirect(cpu);"))
                    elif "jmp" in code or "braf" in code:
                        # The target register is read before the delay slot
                        # runs, so latch it first. A jump table dispatcher
                        # reloads the same register in its slot - emitting the
                        # slot first sends the jump wherever that reload
                        # happened to land, usually address 0.
                        lines.append(f"    {code}")
                        lines.append(f"    {delay_code} /* delay slot */")
                        lines.append(f"    sh4_jump_indirect(cpu);")
                        lines.append(f"    return;")
                    else:
                        lines.append(f"    {code}")
                        lines.append(f"    {delay_code} /* delay slot */")
                else:
                    lines.append(f"    {code}")
                    lines.append(f"    {delay_code} /* delay slot */")

                if returns_to_next:
                    lines.append(f"    goto L_AFTER_SLOT_{pc:08X};")
                    pending_after_slots.setdefault(pc + 4, []).append(pc)
                offset += 2 if slot_is_entry else 4
                continue

            lines.append(f"    {code}")
            offset += 2

        for branch_pc in pending_after_slots.pop(self.offset_to_addr(func_end), ()):
            lines.append(f"L_AFTER_SLOT_{branch_pc:08X}:;")

        # Check if function needs fall-through to next function
        # Find last non-empty code line
        if next_func_addr is not None:
            last_code = ""
            for l in reversed(lines):
                stripped = l.strip()
                if stripped and not stripped.startswith("L_") and stripped != "{":
                    last_code = stripped
                    break
            # If last line is not a terminator, add fall-through call
            if (last_code and
                not last_code.endswith("return;") and
                not last_code.endswith("return; }") and
                not last_code.startswith("goto ") and
                not last_code.startswith("return;")):
                lines.append(f"    func_{next_func_addr:08X}(cpu); /* fall-through */")

        lines.append("}")

        if mids:
            lines.append("")
            lines.append(f"void {func_name}(SH4CPU *cpu) {{ impl_{func_addr:08X}(cpu, 0x{func_addr:08X}u); }}")
            for a in mids:
                lines.append(f"void func_{a:08X}(SH4CPU *cpu) {{ impl_{func_addr:08X}(cpu, 0x{a:08X}u); }}")

        # Post-process: replace any goto to non-existent labels with function calls
        import re
        result_lines = []
        for line in lines:
            m = re.search(r'goto L_([0-9A-Fa-f]{8})', line)
            if m:
                label_addr = int(m.group(1), 16)
                if label_addr not in emitted_labels:
                    # Replace goto with tail call
                    line = re.sub(
                        r'goto L_([0-9A-Fa-f]{8});',
                        lambda match: f'{{ func_{match.group(1)}(cpu); return; }}',
                        line
                    )
            result_lines.append(line)

        return '\n'.join(result_lines)

    def generate_header(self, output_path):
        """Generate the function declarations header."""
        with open(output_path, 'w') as f:
            f.write("/* Auto-generated SH-4 recompiled function declarations */\n")
            f.write("#ifndef GAME_FUNCTIONS_H\n")
            f.write("#define GAME_FUNCTIONS_H\n\n")
            f.write("#include \"recompiler/sh4_cpu.h\"\n\n")
            f.write("/* Indirect call/jump dispatch */\n")
            f.write("void sh4_call_indirect(SH4CPU *cpu);\n")
            f.write("void sh4_jump_indirect(SH4CPU *cpu);\n\n")
            f.write(f"/* {len(self.functions)} recompiled functions */\n")

            for addr in sorted(set(self.functions) | getattr(self, "mid_entry_addrs", set())):
                f.write(f"void func_{addr:08X}(SH4CPU *cpu);\n")

            f.write("\n#endif /* GAME_FUNCTIONS_H */\n")

    def generate_dispatch_table(self, output_path):
        """Generate the address-to-function dispatch table."""
        with open(output_path, 'w') as f:
            f.write("/* Auto-generated function dispatch table */\n")
            f.write("#include \"game/game_functions.h\"\n")
            f.write("#include \"hal/dc_bios.h\"\n")
            f.write("#include <stddef.h>\n\n")

            f.write("typedef struct {\n")
            f.write("    uint32_t addr;\n")
            f.write("    void (*func)(SH4CPU *cpu);\n")
            f.write("} FuncEntry;\n\n")

            f.write(f"static const FuncEntry func_table[] = {{\n")
            for addr in sorted(set(self.functions) | getattr(self, "mid_entry_addrs", set())):
                f.write(f"    {{ 0x{addr:08X}u, func_{addr:08X} }},\n")
            f.write(f"    {{ 0, NULL }}\n")
            f.write("};\n\n")

            f.write(f"#define FUNC_TABLE_SIZE {len(set(self.functions) | getattr(self, 'mid_entry_addrs', set()))}\n\n")

            # Binary search dispatch
            f.write("static void (*find_function(uint32_t addr))(SH4CPU *cpu) {\n")
            f.write("    int lo = 0, hi = FUNC_TABLE_SIZE - 1;\n")
            f.write("    while (lo <= hi) {\n")
            f.write("        int mid = (lo + hi) / 2;\n")
            f.write("        if (func_table[mid].addr == addr) return func_table[mid].func;\n")
            f.write("        if (func_table[mid].addr < addr) lo = mid + 1;\n")
            f.write("        else hi = mid - 1;\n")
            f.write("    }\n")
            f.write("    return NULL;\n")
            f.write("}\n\n")

            f.write("#include <stdio.h>\n")
            f.write("static int unresolved_log = 0;\n\n")
            f.write("/* Recent indirect-call targets. Two stores per call, and the only\n")
            f.write(" * thing that says anything when a crash has damaged the native stack -\n")
            f.write(" * which MSVC's tail calls flatten even when it is intact. */\n")
            f.write("static uint32_t g_trace_pc[256], g_trace_pr[256];\n")
            f.write("static unsigned g_trace_n = 0;\n\n")
            f.write("void dcrecomp_dump_calltrace(int count) {\n")
            f.write("    unsigned total = g_trace_n < 256 ? g_trace_n : 256;\n")
            f.write("    if ((unsigned)count > total) count = (int)total;\n")
            f.write("    printf(\"[TRACE] last %d indirect calls, oldest first:\\n\", count);\n")
            f.write("    for (int i = count - 1; i >= 0; i--) {\n")
            f.write("        unsigned idx = (g_trace_n - 1 - (unsigned)i) & 255;\n")
            f.write("        printf(\"   %08X  (from %08X)\\n\", g_trace_pc[idx], g_trace_pr[idx]);\n")
            f.write("    }\n")
            f.write("    fflush(stdout);\n")
            f.write("}\n\n")
            f.write("void sh4_call_indirect(SH4CPU *cpu) {\n")
            f.write("    /* Normalize: strip P1/P2/P3 area bits to P1 cached form */\n")
            f.write("    uint32_t phys = cpu->pc & 0x1FFFFFFF;\n")
            f.write("    /* A call through the BIOS vector table is a syscall, not\n")
            f.write("     * a function in this binary. */\n")
            f.write("    g_trace_pc[g_trace_n & 255] = cpu->pc;\n")
            f.write("    g_trace_pr[g_trace_n & 255] = cpu->pr;\n")
            f.write("    g_trace_n++;\n")
            f.write("    if (sh4_bios_syscall(cpu)) return;\n")
            f.write("    /* Subsystems the game declared as not runnable here.\n")
            f.write("     * See sh4_stub_function. */\n")
            f.write("    { uint32_t stub_r0;\n")
            f.write("      if (sh4_stubbed_function(cpu->pc, &stub_r0)) {\n")
            f.write("          cpu->r[0] = stub_r0;\n")
            f.write("          return;\n")
            f.write("      } }\n")
            f.write("    uint32_t lookup = phys | 0x80000000;\n")
            f.write("    void (*fn)(SH4CPU *cpu) = find_function(lookup);\n")
            f.write("    /* Code running from a copy of itself: what it computes from its\n")
            f.write("     * own address (MOVA, literals read at run time) is the copy's, see\n")
            f.write("     * sh4_reloc. Every other call runs where it was translated. */\n")
            f.write("    uint32_t outer_reloc = sh4_reloc;\n")
            f.write("    sh4_reloc = 0;\n")
            f.write("    if (!fn) {\n")
            f.write("        /* Handle relocated bootstrap: 0x0C004000-0x0C008000 -> +0xC100 */\n")
            f.write("        if (phys >= 0x0C004000 && phys < 0x0C008000) {\n")
            f.write("            fn = find_function((phys + 0xC100) | 0x80000000);\n")
            f.write("            if (fn) sh4_reloc = (uint32_t)-0xC100;\n")
            f.write("        }\n")
            f.write("    }\n")
            f.write("#ifdef DCRECOMP_ABI_CHECK\n")
            f.write("    /* SH-4 keeps r8-r14 callee-saved. A recompiled function\n")
            f.write("     * that returns with one changed is a recompiler bug - usually\n")
            f.write("     * a branch target with no entry point, stubbed out so the\n")
            f.write("     * epilogue that would have popped them never runs. */\n")
            f.write("    if (fn) {\n")
            f.write("        uint32_t saved[8];\n")
            f.write("        for (int i = 8; i <= 15; i++) saved[i - 8] = cpu->r[i];\n")
            f.write("        uint32_t target = cpu->pc, from = cpu->pr;\n")
            f.write("        fn(cpu);\n")
            f.write("        static int abi_log = 0;\n")
            f.write("        if (cpu->r[15] != saved[7] && abi_log < 50) {\n")
            f.write("            abi_log++;\n")
            f.write("            printf(\"[ABI] func_%08X (from 0x%08X) left the stack unbalanced: SP %08X -> %08X\\n\",\n")
            f.write("                   target | 0x80000000u, from, saved[7], cpu->r[15]);\n")
            f.write("        }\n")
            f.write("        for (int i = 8; i <= 14 && abi_log < 50; i++) {\n")
            f.write("            if (cpu->r[i] != saved[i - 8]) {\n")
            f.write("                abi_log++;\n")
            f.write("                printf(\"[ABI] func_%08X (from 0x%08X) clobbered r%d: %08X -> %08X\\n\",\n")
            f.write("                       target | 0x80000000u, from, i, saved[i - 8], cpu->r[i]);\n")
            f.write("                break;\n")
            f.write("            }\n")
            f.write("        }\n")
            f.write("    }\n")
            f.write("#else\n")
            f.write("    if (fn) { fn(cpu); }\n")
            f.write("#endif\n")
            f.write("    else {\n")
            f.write("        /* BIOS/Boot ROM calls and null pointers are a no-op, but still\n")
            f.write("         * log them: a silently skipped callback is the classic cause of\n")
            f.write("         * a spin loop it was supposed to break. */\n")
            f.write("        if (unresolved_log < 100) {\n")
            f.write("            unresolved_log++;\n")
            f.write("            printf(\"[DISPATCH] unresolved call to 0x%08X (pr=0x%08X)\\n\",\n")
            f.write("                   cpu->pc, cpu->pr);\n")
            f.write("        }\n")
            f.write("    }\n")
            f.write("    sh4_reloc = outer_reloc;\n")
            f.write("}\n\n")

            f.write("void sh4_jump_indirect(SH4CPU *cpu) {\n")
            f.write("    sh4_call_indirect(cpu);\n")
            f.write("}\n")

    def recompile_all(self, output_dir, batch_size=500):
        """Recompile all functions, split into multiple source files."""
        os.makedirs(output_dir, exist_ok=True)

        sorted_funcs = sorted(self.functions.items())
        file_idx = 0
        func_idx = 0
        total_funcs = len(sorted_funcs)

        while func_idx < total_funcs:
            batch = sorted_funcs[func_idx:func_idx + batch_size]
            filepath = os.path.join(output_dir, f"game_code_{file_idx:03d}.c")

            with open(filepath, 'w') as f:
                f.write(f"/* Auto-generated - DO NOT EDIT */\n")
                f.write(f"/* Functions 0x{batch[0][0]:08X} - 0x{batch[-1][0]:08X} */\n")
                f.write(f"#include \"recompiler/sh4_cpu.h\"\n")
                f.write(f"#include \"game/game_functions.h\"\n")
                f.write(f"#include <math.h>\n\n")

                for batch_idx, (addr, info) in enumerate(batch):
                    # Determine next function address for fall-through
                    global_idx = func_idx + batch_idx
                    next_addr = None
                    if global_idx + 1 < total_funcs:
                        next_addr = sorted_funcs[global_idx + 1][0]
                    code = self.recompile_function(addr, info, next_func_addr=next_addr)
                    if code:
                        f.write(code)
                        f.write("\n\n")

            print(f"  Generated {filepath} ({len(batch)} functions)")
            func_idx += batch_size
            file_idx += 1

        print(f"Total: {file_idx} source files, {total_funcs} functions")
        return file_idx


IRQ_ON_SR = False
RUNTIME_LITERALS = False
SP_LOADS = False
SETJMP_SITES = False
RELOCATABLE = False
# Text added to PC-relative addresses under --relocatable.
RELOC_TEXT = ' + sh4_reloc'


def load_entries(path):
    """Read an --entries file: one address per line, '#' starts a comment.

    Only the first token counts, so lines pasted from a run's
    `[DISPATCH] unresolved call to 0x...` log work once trimmed to the address.
    """
    out = set()
    with open(path) as f:
        for line in f:
            tok = line.split('#', 1)[0].split()
            if tok:
                out.add(int(tok[0], 0))
    return out


def main():
    # Parse arguments
    positional = []
    base_addr = LOAD_ADDR
    entries = set()
    args = iter(sys.argv[1:])
    for arg in args:
        if arg == '--base':
            base_addr = int(next(args), 0)
        elif arg == '--entries':
            entries |= load_entries(next(args))
        elif arg == '--irq-on-sr':
            global IRQ_ON_SR
            IRQ_ON_SR = True
        elif arg == '--runtime-literals':
            global RUNTIME_LITERALS
            RUNTIME_LITERALS = True
        elif arg == '--sp-loads':
            global SP_LOADS
            SP_LOADS = True
        elif arg == '--setjmp-sites':
            global SETJMP_SITES
            SETJMP_SITES = True
        elif arg == '--relocatable':
            global RELOCATABLE
            RELOCATABLE = True
        else:
            positional.append(arg)

    binary_path = positional[0] if len(positional) > 0 else "disc_extract/1ST_READ.BIN"
    output_dir = positional[1] if len(positional) > 1 else "src/game"
    header_dir = positional[2] if len(positional) > 2 else "include/game"

    print(f"Loading {binary_path}...")
    with open(binary_path, 'rb') as f:
        data = f.read()

    print(f"Binary: {len(data):,} bytes")
    print(f"Base address: 0x{base_addr:08X}")

    recompiler = SH4Recompiler(data, base_addr, entries)

    print("Finding functions...")
    recompiler.find_functions()

    print("Generating function header...")
    os.makedirs(header_dir, exist_ok=True)
    recompiler.generate_header(os.path.join(header_dir, "game_functions.h"))

    print("Generating dispatch table...")
    os.makedirs(output_dir, exist_ok=True)
    recompiler.generate_dispatch_table(os.path.join(output_dir, "dispatch_table.c"))

    print("Recompiling functions to C...")
    num_files = recompiler.recompile_all(output_dir)

    print(f"\nStatic recompilation complete!")
    print(f"  Functions: {len(recompiler.functions)}")
    print(f"  Mid-function entries: {len(getattr(recompiler, 'mid_entry_addrs', ()))}")
    print(f"  Source files: {num_files}")
    print(f"  Output: {output_dir}/")


if __name__ == '__main__':
    main()
