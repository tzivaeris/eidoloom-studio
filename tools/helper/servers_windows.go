//go:build windows

package main

import (
	"encoding/binary"
	"unsafe"

	"golang.org/x/sys/windows"
)

var getExtendedTcpTable = windows.NewLazySystemDLL("iphlpapi.dll").NewProc("GetExtendedTcpTable")

const tcpTableOwnerPidListener = 3 // TCP_TABLE_OWNER_PID_LISTENER

// listeningPorts: port -> process, from the system's TCP tables (IPv4 and IPv6). netstat's text is translated into the
// system language, so it is not parsed.
func listeningPorts() map[int]int {
	found := map[int]int{}
	for _, family := range []uint32{windows.AF_INET, windows.AF_INET6} {
		size := uint32(0)
		_, _, _ = getExtendedTcpTable.Call(0, uintptr(unsafe.Pointer(&size)), 0, uintptr(family), tcpTableOwnerPidListener, 0)
		if size == 0 {
			continue
		}
		buf := make([]byte, size+4096)
		size = uint32(len(buf))
		ret, _, _ := getExtendedTcpTable.Call(uintptr(unsafe.Pointer(&buf[0])), uintptr(unsafe.Pointer(&size)), 0, uintptr(family), tcpTableOwnerPidListener, 0)
		if ret != 0 {
			continue
		}
		count := int(binary.LittleEndian.Uint32(buf[0:4]))
		row, portAt, pidAt := 24, 8, 20 // MIB_TCPROW_OWNER_PID: state, local addr, local port, remote addr, remote port, pid
		if family == windows.AF_INET6 {
			row, portAt, pidAt = 56, 20, 52 // MIB_TCP6ROW_OWNER_PID: local addr[16], scope, local port, remote addr[16], scope, port, state, pid
		}
		for i := 0; i < count; i++ {
			at := 4 + i*row
			if at+row > len(buf) {
				break
			}
			raw := binary.LittleEndian.Uint32(buf[at+portAt:])
			port := int((raw&0xff)<<8 | (raw>>8)&0xff) // network byte order in the low 16 bits
			found[port] = int(binary.LittleEndian.Uint32(buf[at+pidAt:]))
		}
	}
	return found
}

// processParents: pid -> parent pid, from a snapshot of all processes. A parent that has exited keeps its number here.
func processParents() map[int]int {
	parents := map[int]int{}
	snap, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return parents
	}
	defer windows.CloseHandle(snap)
	var entry windows.ProcessEntry32
	entry.Size = uint32(unsafe.Sizeof(entry))
	for err = windows.Process32First(snap, &entry); err == nil; err = windows.Process32Next(snap, &entry) {
		parents[int(entry.ProcessID)] = int(entry.ParentProcessID)
	}
	return parents
}

// Windows does not let one program read another's working folder, so only the parent chain counts there.
func cwdWithin(pid int, root string) bool { return false }
