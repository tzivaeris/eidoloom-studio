package main

// Web servers on this computer, shown in Studio's Live tab: Studio asks which ports are open ("ports") and sends each
// request of the page ("fetch"), which the helper makes to 127.0.0.1 here and sends back. Only servers started by
// Studio's own commands and terminals count (a process whose parent chain reaches a shell the helper started, or on
// Linux one working inside the folder), so nothing else running on this computer is reachable from Studio.

import (
	"bytes"
	"encoding/base64"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"strings"
	"time"
)

const fetchLimit = 16 << 20 // the answer travels base64 in JSON, under Studio's 25 MB request limit

// remember notes a shell or terminal this helper started; its children and grandchildren count as Studio's.
func (r *Runner) remember(pid int) {
	if pid <= 0 {
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.spawned == nil {
		r.spawned = map[int]time.Time{}
	}
	r.spawned[pid] = time.Now()
	if len(r.spawned) > 2000 { // forget the oldest
		oldest, at := 0, time.Now()
		for p, t := range r.spawned {
			if t.Before(at) {
				oldest, at = p, t
			}
		}
		delete(r.spawned, oldest)
	}
}

// ours: the process, or one of its parents (even one that has exited, whose number the child still carries), is a
// shell this helper started.
func (r *Runner) ours(pid int, parents map[int]int) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	for i := 0; i < 16 && pid > 0; i++ {
		if _, ok := r.spawned[pid]; ok {
			return true
		}
		next, ok := parents[pid]
		if !ok || next == pid {
			return false
		}
		pid = next
	}
	return false
}

// servers: port -> process of each listening server Studio may reach.
func (r *Runner) servers() map[int]int {
	listening := listeningPorts()
	parents := processParents()
	found := map[int]int{}
	for port, pid := range listening {
		if pid == os.Getpid() {
			continue
		}
		if r.ours(pid, parents) || cwdWithin(pid, r.folder.root) {
			found[port] = pid
		}
	}
	return found
}

func (r *Runner) answer(result map[string]any) {
	for i := 0; i < 5; i++ {
		if err := r.folder.client.call("POST", "/api/helper/result", result, nil, 120*time.Second); err == nil {
			return
		}
		time.Sleep(2 * time.Second)
	}
}

func (r *Runner) listPorts(job Job) {
	ports := []int{}
	for port := range r.servers() {
		ports = append(ports, port)
	}
	sort.Ints(ports)
	r.answer(map[string]any{"job": job.ID, "ports": ports})
}

var hopHeaders = map[string]bool{"connection": true, "keep-alive": true, "transfer-encoding": true, "upgrade": true,
	"proxy-connection": true, "te": true, "trailer": true, "host": true, "content-length": true}

func (r *Runner) fetch(job Job) {
	result := map[string]any{"job": job.ID}
	defer func() { r.answer(result) }()
	if _, ok := r.servers()[job.Port]; !ok {
		result["error"] = fmt.Sprintf("no server started from Studio is listening on port %d on this computer", job.Port)
		return
	}
	if !strings.HasPrefix(job.Path, "/") {
		result["error"] = "bad path"
		return
	}
	body, _ := base64.StdEncoding.DecodeString(job.B64)
	client := &http.Client{Timeout: 120 * time.Second, Transport: &http.Transport{DisableCompression: true, Proxy: nil},
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	var resp *http.Response
	var err error
	for _, host := range []string{"127.0.0.1", "[::1]"} { // a server may listen on IPv6 only
		req, e := http.NewRequest(job.Method, fmt.Sprintf("http://%s:%d%s", host, job.Port, job.Path), bytes.NewReader(body))
		if e != nil {
			result["error"] = e.Error()
			return
		}
		for _, h := range job.Headers {
			if len(h) == 2 && !hopHeaders[strings.ToLower(h[0])] {
				req.Header.Add(h[0], h[1])
			}
		}
		req.Host = fmt.Sprintf("127.0.0.1:%d", job.Port)
		if resp, err = client.Do(req); err == nil {
			break
		}
	}
	if err != nil {
		result["error"] = fmt.Sprintf("nothing answered on port %d: %v", job.Port, err)
		return
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, fetchLimit+1))
	if err != nil {
		result["error"] = "the server stopped while answering: " + err.Error()
		return
	}
	if len(data) > fetchLimit {
		result["error"] = fmt.Sprintf("the answer is larger than %d MB", fetchLimit>>20)
		return
	}
	headers := [][2]string{}
	for name, values := range resp.Header {
		if hopHeaders[strings.ToLower(name)] {
			continue
		}
		for _, v := range values {
			headers = append(headers, [2]string{name, v})
		}
	}
	result["status"], result["reason"], result["headers"], result["b64"] = resp.StatusCode, http.StatusText(resp.StatusCode), headers, base64.StdEncoding.EncodeToString(data)
}
