//go:build !windows

package main

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// listeningPorts: port -> process, for listening TCP sockets, from /proc.
func listeningPorts() map[int]int {
	inodes := map[string]int{}
	for _, name := range []string{"/proc/net/tcp", "/proc/net/tcp6"} {
		data, err := os.ReadFile(name)
		if err != nil {
			continue
		}
		for _, line := range strings.Split(string(data), "\n") {
			f := strings.Fields(line)
			if len(f) > 9 && f[3] == "0A" {
				if i := strings.LastIndex(f[1], ":"); i >= 0 {
					if port, err := strconv.ParseInt(f[1][i+1:], 16, 32); err == nil {
						inodes[f[9]] = int(port)
					}
				}
			}
		}
	}
	found := map[int]int{}
	procs, _ := os.ReadDir("/proc")
	for _, p := range procs {
		pid, err := strconv.Atoi(p.Name())
		if err != nil {
			continue
		}
		fds, err := os.ReadDir(filepath.Join("/proc", p.Name(), "fd"))
		if err != nil {
			continue
		}
		for _, fd := range fds {
			link, err := os.Readlink(filepath.Join("/proc", p.Name(), "fd", fd.Name()))
			if err == nil && strings.HasPrefix(link, "socket:[") {
				if port, ok := inodes[strings.TrimSuffix(link[8:], "]")]; ok {
					found[port] = pid
				}
			}
		}
	}
	return found
}

// processParents: pid -> parent pid.
func processParents() map[int]int {
	parents := map[int]int{}
	procs, _ := os.ReadDir("/proc")
	for _, p := range procs {
		pid, err := strconv.Atoi(p.Name())
		if err != nil {
			continue
		}
		data, err := os.ReadFile(filepath.Join("/proc", p.Name(), "stat"))
		if err != nil {
			continue
		}
		s := string(data)
		if i := strings.LastIndex(s, ")"); i >= 0 {
			if f := strings.Fields(s[i+1:]); len(f) > 1 {
				if ppid, err := strconv.Atoi(f[1]); err == nil {
					parents[pid] = ppid
				}
			}
		}
	}
	return parents
}

// cwdWithin: the process works inside the folder (as Studio itself checks for folders on its own machine).
func cwdWithin(pid int, root string) bool {
	cwd, err := os.Readlink(filepath.Join("/proc", strconv.Itoa(pid), "cwd"))
	if err != nil {
		return false
	}
	real, err := filepath.EvalSymlinks(root)
	if err != nil {
		real = root
	}
	return cwd == real || strings.HasPrefix(cwd, real+string(os.PathSeparator))
}
