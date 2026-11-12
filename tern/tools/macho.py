"""Minimal Mach-O (arm64, 64-bit, thin) reader used by the Tern localisation tools."""
import struct

LC_SEGMENT_64 = 0x19


class Section:
    def __init__(self, seg, name, addr, size, offset):
        self.seg, self.name, self.addr, self.size, self.offset = seg, name, addr, size, offset

    def contains(self, a):
        return self.addr <= a < self.addr + self.size


class MachO:
    def __init__(self, data):
        self.data = data
        magic, _, _, _, ncmds, sizeofcmds, _, _ = struct.unpack_from("<IiiIIIII", data, 0)
        assert magic == 0xFEEDFACF, "not a thin 64-bit Mach-O"
        self.ncmds, self.sizeofcmds = ncmds, sizeofcmds
        self.cmds = []  # (offset, cmd, size)
        self.segments = []  # dict
        self.sections = {}
        off = 32
        for _ in range(ncmds):
            cmd, size = struct.unpack_from("<II", data, off)
            self.cmds.append((off, cmd, size))
            if cmd == LC_SEGMENT_64:
                name = data[off + 8:off + 24].rstrip(b"\0").decode()
                vmaddr, vmsize, fileoff, filesize, _, _, nsects, _ = struct.unpack_from("<QQQQiiII", data, off + 24)
                self.segments.append(dict(name=name, vmaddr=vmaddr, vmsize=vmsize, fileoff=fileoff,
                                          filesize=filesize, cmdoff=off, nsects=nsects))
                so = off + 72
                for _ in range(nsects):
                    sname = data[so:so + 16].rstrip(b"\0").decode()
                    addr, ssize, soff = struct.unpack_from("<QQI", data, so + 32)
                    self.sections[(name, sname)] = Section(name, sname, addr, ssize, soff)
                    so += 80
            off += size

    def sect(self, seg, name):
        return self.sections[(seg, name)]

    def seg(self, name):
        return next(s for s in self.segments if s["name"] == name)

    def addr_to_off(self, addr):
        for s in self.segments:
            if s["vmaddr"] <= addr < s["vmaddr"] + s["filesize"]:
                return addr - s["vmaddr"] + s["fileoff"]
        raise ValueError(hex(addr))
