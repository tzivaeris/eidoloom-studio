// studio-helper connects a folder on this computer to Eidoloom Studio. Studio keeps a copy of the folder in sync and
// runs the agent's commands and the terminals here, in the folder, with this computer's own programs.
//
// Usage:
//
//	studio-helper                 run every saved connection (the first time, pair one)
//	studio-helper pair            pair another folder: Studio's address, a pairing code from Studio, the folder
//	studio-helper list            show the saved connections
//	studio-helper remove N        forget connection N (remove it in Studio too)
//
// Pairing without questions: studio-helper pair -server URL -code CODE -folder PATH [-trust]
package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha1"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

var version = "dev" // set by build.sh from VERSION

type Connection struct {
	Server string `json:"server"`
	ID     string `json:"id"`
	Key    string `json:"key"`
	Folder string `json:"folder"`
	Pin    string `json:"pin,omitempty"` // sha256 of the server's certificate, when it is not one this computer trusts
	Name   string `json:"name"`
}

type Config struct {
	Connections  []Connection `json:"connections"`
	AutostartSet bool         `json:"autostart_set,omitempty"` // turned on once, after the first pairing; the person decides after that
}

var configMu sync.Mutex

func configDir() string {
	if d := os.Getenv("STUDIO_HELPER_HOME"); d != "" {
		return d
	}
	base, err := os.UserConfigDir()
	if err != nil {
		base = "."
	}
	return filepath.Join(base, "strata-studio-helper")
}

func loadConfig() Config {
	var c Config
	data, err := os.ReadFile(filepath.Join(configDir(), "config.json"))
	if err == nil {
		_ = json.Unmarshal(data, &c)
	}
	return c
}

func saveConfig(c Config) error {
	configMu.Lock()
	defer configMu.Unlock()
	if err := os.MkdirAll(configDir(), 0o700); err != nil {
		return err
	}
	data, _ := json.MarshalIndent(c, "", "  ")
	return writeAtomic(filepath.Join(configDir(), "config.json"), data, 0o600)
}

func writeAtomic(path string, data []byte, mode os.FileMode) error {
	tmp := path + ".studio-tmp"
	if err := os.WriteFile(tmp, data, mode); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// ---- talking to Studio

type Client struct {
	conn Connection
	http *http.Client
}

func tlsFor(pin string) *tls.Config {
	if pin == "" {
		return &tls.Config{}
	}
	return &tls.Config{InsecureSkipVerify: true, VerifyConnection: func(cs tls.ConnectionState) error {
		if len(cs.PeerCertificates) == 0 {
			return errors.New("no certificate")
		}
		sum := sha256.Sum256(cs.PeerCertificates[0].Raw)
		if hex.EncodeToString(sum[:]) != pin {
			return errors.New("the server's certificate changed since pairing; pair again if this is expected")
		}
		return nil
	}}
}

func newClient(conn Connection) *Client {
	tr := &http.Transport{TLSClientConfig: tlsFor(conn.Pin), Proxy: http.ProxyFromEnvironment, MaxIdleConnsPerHost: 4}
	return &Client{conn: conn, http: &http.Client{Transport: tr, Timeout: 120 * time.Second}}
}

type apiError struct {
	Status int
	Text   string
}

func (e *apiError) Error() string { return fmt.Sprintf("%d: %s", e.Status, e.Text) }

func (c *Client) call(method, path string, body any, out any, timeout time.Duration) error {
	var reader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(data)
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(c.conn.Server, "/")+path, reader)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Studio", "1")
	req.Header.Set("User-Agent", "studio-helper/"+version)
	if c.conn.Key != "" {
		req.Header.Set("X-Helper-Key", c.conn.Key)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 300 {
		var e struct {
			Error string `json:"error"`
		}
		_ = json.Unmarshal(data, &e)
		if e.Error == "" {
			e.Error = strings.TrimSpace(string(data))
		}
		return &apiError{resp.StatusCode, e.Error}
	}
	if out != nil {
		return json.Unmarshal(data, out)
	}
	return nil
}

// ---- the folder: what is shared with Studio, and the state both sides agreed on last

type fileState struct {
	Size  int64
	Mtime int64
	Sha   string
}

type Folder struct {
	root    string
	client  *Client
	mu      sync.Mutex // one sync at a time
	base    map[string]string
	cache   map[string]fileState
	skip    map[string]bool
	maxFile int64
	skipped []string
	label   string
}

func (f *Folder) basePath() string { return filepath.Join(configDir(), "base-"+f.client.conn.ID+".json") }

func (f *Folder) loadBase() {
	f.base = map[string]string{}
	if data, err := os.ReadFile(f.basePath()); err == nil {
		_ = json.Unmarshal(data, &f.base)
	}
}

func (f *Folder) saveBase() {
	data, _ := json.Marshal(f.base)
	_ = writeAtomic(f.basePath(), data, 0o600)
}

func (f *Folder) skipped_(rel string) bool {
	parts := strings.Split(rel, "/")
	for _, p := range parts {
		if f.skip[p] {
			return true
		}
	}
	if strings.HasPrefix(rel, ".strata-studio/conflicts/") {
		return true
	}
	return parts[0] == ".git" && strings.HasSuffix(parts[len(parts)-1], ".lock")
}

// scan answers path -> sha1 for every shared file, reading only files whose size or time changed.
func (f *Folder) scan() map[string]string {
	found := map[string]string{}
	var big []string
	_ = filepath.WalkDir(f.root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if path == f.root {
			return nil
		}
		rel := filepath.ToSlash(strings.TrimPrefix(path, f.root+string(filepath.Separator)))
		if d.Type()&fs.ModeSymlink != 0 {
			return nil // never followed, never copied
		}
		if d.IsDir() {
			if f.skip[d.Name()] {
				return filepath.SkipDir
			}
			return nil
		}
		if !d.Type().IsRegular() || f.skipped_(rel) {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return nil
		}
		if info.Size() > f.maxFile {
			big = append(big, fmt.Sprintf("%s (%d MB)", rel, info.Size()/1048576))
			return nil
		}
		st := fileState{Size: info.Size(), Mtime: info.ModTime().UnixNano()}
		if hit, ok := f.cache[rel]; ok && hit.Size == st.Size && hit.Mtime == st.Mtime {
			found[rel] = hit.Sha
			return nil
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return nil
		}
		sum := sha1.Sum(data)
		st.Sha = hex.EncodeToString(sum[:])
		f.cache[rel] = st
		found[rel] = st.Sha
		return nil
	})
	sort.Strings(big)
	f.skipped = big
	return found
}

// safePath turns a path from Studio into one inside the folder, refusing anything that could leave it.
func (f *Folder) safePath(rel string) (string, error) {
	if rel == "" || strings.ContainsAny(rel, "\x00\\") || strings.HasPrefix(rel, "/") || f.skipped_(rel) {
		return "", fmt.Errorf("refused path %q", rel)
	}
	for _, p := range strings.Split(rel, "/") {
		if p == "" || p == "." || p == ".." || strings.Contains(p, ":") {
			return "", fmt.Errorf("refused path %q", rel)
		}
	}
	full := filepath.Join(f.root, filepath.FromSlash(rel))
	realRoot, err := filepath.EvalSymlinks(f.root)
	if err != nil {
		return "", err
	}
	// The deepest existing parent must still be inside the folder after following links.
	parent := filepath.Dir(full)
	for {
		if _, err := os.Lstat(parent); err == nil {
			break
		}
		parent = filepath.Dir(parent)
	}
	realParent, err := filepath.EvalSymlinks(parent)
	if err != nil {
		return "", err
	}
	if realParent != realRoot && !strings.HasPrefix(realParent, realRoot+string(filepath.Separator)) {
		return "", fmt.Errorf("refused path %q: it leads outside the folder", rel)
	}
	return full, nil
}

type syncAnswer struct {
	Ops       map[string]*struct{ Sha, B64 string } `json:"ops"`
	More      bool                                  `json:"more"`
	Conflicts []string                              `json:"conflicts"`
}

const batchBytes = 8 * 1024 * 1024

// sync sends this computer's changes since the agreed state and applies Studio's, until neither side has more.
func (f *Folder) sync() error {
	f.mu.Lock()
	defer f.mu.Unlock()
	for round := 0; round < 1000; round++ {
		current := f.scan()
		changes := map[string]any{}
		sent := map[string]string{}
		size, more := 0, false
		keys := make([]string, 0, len(current))
		for rel := range current {
			keys = append(keys, rel)
		}
		sort.Strings(keys)
		for _, rel := range keys {
			sha := current[rel]
			if f.base[rel] == sha {
				continue
			}
			data, err := os.ReadFile(filepath.Join(f.root, filepath.FromSlash(rel)))
			if err != nil {
				continue
			}
			if size > 0 && size+len(data) > batchBytes {
				more = true
				break
			}
			changes[rel] = map[string]string{"b64": base64.StdEncoding.EncodeToString(data)}
			sent[rel] = sha
			size += len(data)
		}
		for rel := range f.base {
			if _, ok := current[rel]; !ok && !more {
				changes[rel] = nil
				sent[rel] = ""
			}
		}
		var answer syncAnswer
		if err := f.client.call("POST", "/api/helper/sync", map[string]any{"changes": changes, "skipped": f.skipped}, &answer, 300*time.Second); err != nil {
			return err
		}
		for rel, sha := range sent {
			if sha == "" {
				delete(f.base, rel)
			} else {
				f.base[rel] = sha
			}
		}
		for rel, op := range answer.Ops {
			full, err := f.safePath(rel)
			if err != nil {
				logf(f.label, "%v", err)
				continue
			}
			if op == nil {
				if info, err := os.Lstat(full); err == nil && !info.IsDir() {
					_ = os.Remove(full)
					removeEmptyParents(f.root, filepath.Dir(full))
				}
				delete(f.base, rel)
				delete(f.cache, rel)
				continue
			}
			data, err := base64.StdEncoding.DecodeString(op.B64)
			if err != nil {
				continue
			}
			if info, err := os.Lstat(full); err == nil && (info.Mode()&fs.ModeSymlink != 0 || info.IsDir()) {
				_ = os.RemoveAll(full)
			}
			if err := os.MkdirAll(filepath.Dir(full), 0o755); err != nil {
				logf(f.label, "cannot write %s: %v", rel, err)
				continue
			}
			mode := os.FileMode(0o644)
			if info, err := os.Stat(full); err == nil {
				mode = info.Mode().Perm()
			}
			if err := writeAtomic(full, data, mode); err != nil {
				logf(f.label, "cannot write %s: %v", rel, err)
				continue
			}
			f.base[rel] = op.Sha
			if info, err := os.Stat(full); err == nil {
				f.cache[rel] = fileState{Size: info.Size(), Mtime: info.ModTime().UnixNano(), Sha: op.Sha}
			}
		}
		for _, rel := range answer.Conflicts {
			logf(f.label, "%s changed here and in Studio; this computer's version was kept, Studio's is in .strata-studio/conflicts", rel)
		}
		f.saveBase()
		if !more && !answer.More {
			return nil
		}
	}
	return nil
}

func removeEmptyParents(root, dir string) {
	for dir != root && strings.HasPrefix(dir, root) {
		if err := os.Remove(dir); err != nil {
			return
		}
		dir = filepath.Dir(dir)
	}
}

// ---- jobs from Studio

type Job struct {
	ID      string            `json:"id"`
	Type    string            `json:"type"`
	Script  string            `json:"script"`
	Command string            `json:"command"`
	Env     map[string]string `json:"env"`
	Timeout int               `json:"timeout"`
	Job     string            `json:"job"`
	Term    string            `json:"term"`
	Cols    int               `json:"cols"`
	Rows    int               `json:"rows"`
	B64     string            `json:"b64"`
	Port    int               `json:"port"`
	Method  string            `json:"method"`
	Path    string            `json:"path"`
	Headers [][]string        `json:"headers"`
}

type Runner struct {
	folder *Folder
	mu     sync.Mutex
	cancel map[string]context.CancelFunc
	terms  map[string]Pty
	// shells and terminals this helper started: servers they start are the ones Studio may show (servers.go)
	spawned map[int]time.Time
}

const outputLimit = 200 * 1024

func (r *Runner) run(job Job) {
	if err := r.folder.sync(); err != nil {
		logf(r.folder.label, "sync before the command failed: %v", err)
	}
	timeout := time.Duration(job.Timeout) * time.Second
	if timeout <= 0 {
		timeout = 120 * time.Second
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	r.mu.Lock()
	r.cancel[job.ID] = cancel
	r.mu.Unlock()
	defer func() {
		cancel()
		r.mu.Lock()
		delete(r.cancel, job.ID)
		r.mu.Unlock()
	}()
	cmd := shellCommand(job)
	cmd.Dir = r.folder.root
	cmd.Env = os.Environ()
	for k, v := range job.Env {
		cmd.Env = append(cmd.Env, k+"="+v)
	}
	var out tailBuffer
	cmd.Stdout, cmd.Stderr = &out, &out
	cmd.Stdin = nil
	prepare(cmd)
	// A server started in the background keeps the output pipe open after the command ends; without this the command
	// waited for the server and Studio said the computer did not answer in time.
	cmd.WaitDelay = 3 * time.Second
	shown := job.Command
	if v, ok := job.Env["STUDIO_CMD"]; ok {
		shown = v
	}
	logf(r.folder.label, "running: %s", oneLine(shown))
	code, timedOut := 0, false
	if err := cmd.Start(); err != nil {
		out.Write([]byte("could not start the shell: " + err.Error() + "\n"))
		code = 127
	} else {
		r.remember(cmd.Process.Pid)
		done := make(chan error, 1)
		go func() { done <- cmd.Wait() }()
		select {
		case err := <-done:
			code = exitCode(err)
			if errors.Is(err, exec.ErrWaitDelay) {
				code = 0 // the command itself finished; only something it started in the background kept the pipe open
			}
		case <-ctx.Done():
			killTree(cmd)
			<-done
			code, timedOut = -1, errors.Is(ctx.Err(), context.DeadlineExceeded)
		}
	}
	if err := r.folder.sync(); err != nil {
		logf(r.folder.label, "sync after the command failed: %v", err)
	}
	result := map[string]any{"job": job.ID, "code": code, "output": out.String(), "timed_out": timedOut}
	for i := 0; i < 5; i++ {
		if err := r.folder.client.call("POST", "/api/helper/result", result, nil, 60*time.Second); err == nil {
			return
		}
		time.Sleep(2 * time.Second)
	}
}

func exitCode(err error) int {
	if err == nil {
		return 0
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		return int(int32(ee.ExitCode())) // Windows reports NTSTATUS codes such as 0xC000013A as large unsigned numbers
	}
	return 1
}

type tailBuffer struct {
	mu  sync.Mutex
	buf []byte
	cut bool
}

func (t *tailBuffer) Write(p []byte) (int, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.buf = append(t.buf, p...)
	if len(t.buf) > outputLimit {
		t.buf = t.buf[len(t.buf)-outputLimit:]
		t.cut = true
	}
	return len(p), nil
}

func (t *tailBuffer) String() string {
	t.mu.Lock()
	defer t.mu.Unlock()
	return strings.ToValidUTF8(string(t.buf), "?")
}

func oneLine(s string) string {
	s = strings.ReplaceAll(s, "\n", " ⏎ ")
	if len(s) > 100 {
		return s[:100] + "…"
	}
	return s
}

func (r *Runner) handle(job Job) {
	switch job.Type {
	case "run":
		go r.run(job)
	case "kill":
		r.mu.Lock()
		if c := r.cancel[job.Job]; c != nil {
			c()
		}
		r.mu.Unlock()
	case "sync":
		go func() { _ = r.folder.sync() }()
	case "ports":
		go r.listPorts(job)
	case "fetch":
		go r.fetch(job)
	case "term_open":
		go r.termOpen(job)
	case "term_in":
		if t := r.term(job.Term); t != nil {
			data, _ := base64.StdEncoding.DecodeString(job.B64)
			_, _ = t.Write(data)
		}
	case "term_resize":
		if t := r.term(job.Term); t != nil {
			_ = t.Resize(job.Cols, job.Rows)
		}
	case "term_close":
		if t := r.term(job.Term); t != nil {
			t.Close()
		}
	}
}

func (r *Runner) term(id string) Pty {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.terms[id]
}

func (r *Runner) termOpen(job Job) {
	cols, rows := job.Cols, job.Rows
	if cols <= 0 {
		cols = 80
	}
	if rows <= 0 {
		rows = 24
	}
	t, err := startPty(r.folder.root, cols, rows)
	send := func(body map[string]any) {
		body["term"] = job.Term
		_ = r.folder.client.call("POST", "/api/helper/term", body, nil, 30*time.Second)
	}
	if err != nil {
		send(map[string]any{"b64": base64.StdEncoding.EncodeToString([]byte("Could not start a terminal here: " + err.Error() + "\r\n")), "exit": -1})
		return
	}
	r.remember(t.Pid())
	logf(r.folder.label, "terminal opened")
	r.mu.Lock()
	r.terms[job.Term] = t
	r.mu.Unlock()
	// Output goes out in small batches, so typing feels live without a request per character.
	chunks := make(chan []byte, 256)
	go func() {
		buf := make([]byte, 32*1024)
		for {
			n, err := t.Read(buf)
			if n > 0 {
				chunks <- append([]byte(nil), buf[:n]...)
			}
			if err != nil {
				close(chunks)
				return
			}
		}
	}()
	var pending []byte
	flush := func() {
		if len(pending) > 0 {
			send(map[string]any{"b64": base64.StdEncoding.EncodeToString(pending)})
			pending = nil
		}
	}
	tick := time.NewTicker(30 * time.Millisecond)
	defer tick.Stop()
	for open := true; open; {
		select {
		case data, ok := <-chunks:
			if !ok {
				open = false
				break
			}
			pending = append(pending, data...)
			if len(pending) > 64*1024 {
				flush()
			}
		case <-tick.C:
			flush()
		}
	}
	flush()
	code := t.Wait()
	r.mu.Lock()
	delete(r.terms, job.Term)
	r.mu.Unlock()
	send(map[string]any{"exit": code})
	logf(r.folder.label, "terminal closed")
}

// ---- one connection: keep asking Studio for work, and keep the folder in sync

func serve(ctx context.Context, conn Connection, live *Live) {
	client := newClient(conn)
	label := conn.Name
	folder := &Folder{root: conn.Folder, client: client, cache: map[string]fileState{}, skip: map[string]bool{}, maxFile: 25 * 1024 * 1024, label: label}
	folder.loadBase()
	runner := &Runner{folder: folder, cancel: map[string]context.CancelFunc{}, terms: map[string]Pty{}}
	live.set("connecting", "")
	if info, err := os.Stat(conn.Folder); err != nil || !info.IsDir() {
		logf(label, "the folder %s is missing; this connection is paused", conn.Folder)
		live.set("stopped", "the folder is missing on this computer")
		return
	}
	online := false
	go func() {
		for ctx.Err() == nil {
			if online {
				if err := folder.sync(); err != nil && online {
					logf(label, "sync: %v", err)
				} else if err == nil {
					live.synced(len(folder.base), folder.skipped)
				}
			}
			time.Sleep(2 * time.Second)
		}
	}()
	defer runner.closeAll()
	for ctx.Err() == nil {
		var answer struct {
			Jobs    []Job    `json:"jobs"`
			Skip    []string `json:"skip"`
			MaxFile int64    `json:"max_file"`
		}
		wait := "25"
		if !online {
			wait = "0" // the first answer comes at once, so the first sync does not wait for a job
		}
		err := client.call("GET", "/api/helper/poll?wait="+wait, nil, &answer, 45*time.Second)
		if err != nil {
			var ae *apiError
			if errors.As(err, &ae) && (ae.Status == 401 || ae.Status == 410) {
				logf(label, "Studio no longer knows this folder (%s). Remove it here, and connect it again if you want it.", ae.Text)
				live.set("stopped", "Studio no longer knows this folder: "+ae.Text)
				return
			}
			if online {
				logf(label, "lost the connection to Studio (%v); trying again", err)
			}
			if online || live.status() == "connecting" {
				live.set("offline", "cannot reach Studio: "+shortErr(err))
			}
			online = false
			time.Sleep(3 * time.Second)
			continue
		}
		folder.mu.Lock()
		for _, name := range answer.Skip {
			folder.skip[name] = true
		}
		if answer.MaxFile > 0 {
			folder.maxFile = answer.MaxFile
		}
		folder.mu.Unlock()
		if !online {
			online = true
			live.set("connected", "")
			if err := folder.sync(); err != nil {
				logf(label, "first sync failed: %v", err)
			} else {
				live.synced(len(folder.base), folder.skipped)
				logf(label, "connected to %s, sharing %s (%d files in sync)", conn.Server, conn.Folder, len(folder.base))
			}
		}
		for _, job := range answer.Jobs {
			runner.handle(job)
		}
	}
}

var (
	logMu    sync.Mutex
	logLines []string
	logFile  *os.File
)

// logf prints a line, keeps the newest 300 for the form, and appends to helper.log beside the settings.
func logf(label, format string, args ...any) {
	logMu.Lock()
	defer logMu.Unlock()
	line := fmt.Sprintf("%s [%s] %s", time.Now().Format("2006-01-02 15:04:05"), label, fmt.Sprintf(format, args...))
	fmt.Println(line)
	logLines = append(logLines, line)
	if len(logLines) > 300 {
		logLines = logLines[len(logLines)-300:]
	}
	if logFile == nil {
		_ = os.MkdirAll(configDir(), 0o700)
		path := filepath.Join(configDir(), "helper.log")
		if info, err := os.Stat(path); err == nil && info.Size() > 2*1024*1024 {
			_ = os.Rename(path, path+".old")
		}
		logFile, _ = os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	}
	if logFile != nil {
		_, _ = logFile.WriteString(line + "\n")
	}
}

func recentLog() []string {
	logMu.Lock()
	defer logMu.Unlock()
	return append([]string(nil), logLines...)
}

func shortErr(err error) string {
	text := err.Error()
	if i := strings.LastIndex(text, ": "); i > 0 && len(text) > 120 {
		text = text[i+2:]
	}
	return text
}

// ---- pairing

var stdin = bufio.NewReader(os.Stdin)

func ask(prompt, fallback string) string {
	if fallback != "" {
		fmt.Printf("%s [%s]: ", prompt, fallback)
	} else {
		fmt.Printf("%s: ", prompt)
	}
	line, _ := stdin.ReadString('\n')
	line = strings.TrimSpace(line)
	if line == "" {
		return fallback
	}
	return line
}

// certificatePin checks the server's certificate. One this computer does not trust can still be pinned, after the
// person compares its fingerprint with the one Studio shows.
func certificatePin(server string, trust bool) (string, error) {
	pin, untrusted, err := certificateCheck(server)
	if err != nil || !untrusted || trust {
		return pin, err
	}
	fmt.Printf("\nThis computer does not know the certificate of %s.\nIts fingerprint is:\n  %s\n", server, groups(pin))
	fmt.Println("Trust it only if this is the address of your own Studio, on your own network.")
	if strings.ToLower(ask("Trust it? (yes/no)", "no")) != "yes" {
		return "", errors.New("not trusted")
	}
	return pin, nil
}

// certificateCheck answers the pin to keep for a server whose certificate this computer does not trust ("" when it does).
func certificateCheck(server string) (pin string, untrusted bool, err error) {
	u, err := url.Parse(server)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" {
		return "", false, fmt.Errorf("Studio's address must look like https://studio.local")
	}
	if u.Scheme == "http" {
		return "", false, nil
	}
	host := u.Host
	if !strings.Contains(host, ":") {
		host += ":443"
	}
	dialer := &net.Dialer{Timeout: 10 * time.Second}
	conn, err := tls.DialWithDialer(dialer, "tcp", host, &tls.Config{})
	if err == nil {
		conn.Close()
		return "", false, nil
	}
	var unknown x509.UnknownAuthorityError
	var invalid x509.CertificateInvalidError
	var hostErr x509.HostnameError
	if !errors.As(err, &unknown) && !errors.As(err, &invalid) && !errors.As(err, &hostErr) && !strings.Contains(err.Error(), "certificate") {
		return "", false, fmt.Errorf("cannot reach %s: %v", server, err)
	}
	raw, err := tls.DialWithDialer(dialer, "tcp", host, &tls.Config{InsecureSkipVerify: true})
	if err != nil {
		return "", false, fmt.Errorf("cannot reach %s: %v", server, err)
	}
	defer raw.Close()
	sum := sha256.Sum256(raw.ConnectionState().PeerCertificates[0].Raw)
	return hex.EncodeToString(sum[:]), true, nil
}

func groups(hexs string) string {
	var parts []string
	for i := 0; i < len(hexs); i += 4 {
		end := i + 4
		if end > len(hexs) {
			end = len(hexs)
		}
		parts = append(parts, hexs[i:end])
	}
	return strings.Join(parts, " ")
}

func pair(args []string) error {
	fl := flag.NewFlagSet("pair", flag.ExitOnError)
	server := fl.String("server", "", "Studio's address, such as https://studio.local")
	code := fl.String("code", "", "the pairing code Studio shows")
	folder := fl.String("folder", "", "the folder to share")
	trust := fl.Bool("trust", false, "trust the server's certificate without asking")
	_ = fl.Parse(args)
	cfg := loadConfig()
	_ = cfg
	if *server == "" {
		last := "https://studio.local"
		if n := len(cfg.Connections); n > 0 {
			last = cfg.Connections[n-1].Server
		}
		*server = ask("Studio's address", last)
	}
	*server = strings.TrimRight(*server, "/")
	pin, err := certificatePin(*server, *trust)
	if err != nil {
		return err
	}
	if *code == "" {
		*code = ask("Pairing code (in Studio: Open folder → Connect a folder on my computer)", "")
	}
	if *folder == "" {
		here, _ := os.Getwd()
		*folder = ask("Folder to share", here)
	}
	c, err := register(*server, *code, *folder, pin)
	if err != nil {
		return err
	}
	fmt.Printf("\nPaired: %s is \"%s\" in Studio.\n", c.Folder, c.Name)
	return nil
}

// register pairs a folder with Studio and saves the connection.
func register(server, code, folder, pin string) (Connection, error) {
	server = strings.TrimRight(strings.TrimSpace(server), "/")
	abs, err := filepath.Abs(strings.TrimSpace(folder))
	if err != nil || strings.TrimSpace(folder) == "" {
		return Connection{}, errors.New("choose the folder to share")
	}
	if info, err := os.Stat(abs); err != nil || !info.IsDir() {
		return Connection{}, fmt.Errorf("%s is not a folder", abs)
	}
	if real, err := filepath.EvalSymlinks(abs); err == nil {
		abs = real
	}
	home, _ := os.UserHomeDir()
	if abs == filepath.VolumeName(abs)+string(filepath.Separator) || (home != "" && abs == home) {
		return Connection{}, fmt.Errorf("share a project folder, not %s itself", abs)
	}
	for _, c := range loadConfig().Connections {
		if c.Folder == abs && c.Server == server {
			return Connection{}, fmt.Errorf("%s is already connected to this Studio", abs)
		}
	}
	machine, _ := os.Hostname()
	client := newClient(Connection{Server: server, Pin: pin})
	var answer struct {
		ID     string `json:"id"`
		Key    string `json:"key"`
		Folder string `json:"folder"`
	}
	body := map[string]string{"code": strings.TrimSpace(code), "machine": machine, "os": osName(), "shell": shellName(), "folder": abs}
	if err := client.call("POST", "/api/helper/register", body, &answer, 60*time.Second); err != nil {
		var ae *apiError
		if errors.As(err, &ae) {
			return Connection{}, errors.New(ae.Text)
		}
		return Connection{}, fmt.Errorf("cannot reach Studio at %s: %s", server, shortErr(err))
	}
	conn := Connection{Server: server, ID: answer.ID, Key: answer.Key, Folder: abs, Pin: pin, Name: answer.Folder}
	cfg := loadConfig()
	cfg.Connections = append(cfg.Connections, conn)
	if err := saveConfig(cfg); err != nil {
		return Connection{}, err
	}
	return conn, nil
}

// forget removes a saved connection and its sync state.
// tellStudioGone tells Studio that this folder stops being shared, so Studio's My computers drops it at once and keeps
// its copy as an ordinary folder (0.07). Best effort: when Studio cannot be reached, it shows the folder as offline,
// and it can be disconnected there too.
func tellStudioGone(id string) bool {
	for _, c := range loadConfig().Connections {
		if c.ID == id {
			err := newClient(c).call("POST", "/api/helper/leave", map[string]any{}, nil, 15*time.Second)
			var ae *apiError
			if errors.As(err, &ae) && (ae.Status == 401 || ae.Status == 410) {
				return true // Studio no longer has this folder: nothing left to tell it
			}
			return err == nil
		}
	}
	return false
}

func forget(id string) (Connection, bool) {
	cfg := loadConfig()
	for i, c := range cfg.Connections {
		if c.ID == id {
			cfg.Connections = append(cfg.Connections[:i], cfg.Connections[i+1:]...)
			_ = os.Remove(filepath.Join(configDir(), "base-"+id+".json"))
			_ = saveConfig(cfg)
			return c, true
		}
	}
	return Connection{}, false
}

func osName() string {
	name := map[string]string{"windows": "Windows", "linux": "Linux", "darwin": "macOS"}[runtime.GOOS]
	if name == "" {
		name = runtime.GOOS
	}
	return name + " " + runtime.GOARCH
}

func main() {
	args := os.Args[1:]
	background, noTray := false, os.Getenv("STUDIO_HELPER_NO_TRAY") != ""
	var rest []string
	for _, a := range args {
		switch a {
		case "--background":
			background = true
		case "--no-tray":
			noTray = true
		default:
			rest = append(rest, a)
		}
	}
	if len(rest) == 0 {
		runApp(background, noTray)
		return
	}
	fail := func(err error) {
		fmt.Println("\nError:", err)
		os.Exit(1)
	}
	switch rest[0] {
	case "pair":
		if err := pair(rest[1:]); err != nil {
			fail(err)
		}
	case "list":
		for i, c := range loadConfig().Connections {
			fmt.Printf("%d. %s  →  %s (%s)\n", i+1, c.Folder, c.Name, c.Server)
		}
	case "remove":
		cfg := loadConfig()
		n, err := strconv.Atoi(strings.TrimSpace(strings.Join(rest[1:], "")))
		if err != nil || n < 1 || n > len(cfg.Connections) {
			fail(errors.New("say which one: studio-helper remove N (see studio-helper list)"))
		}
		told := tellStudioGone(cfg.Connections[n-1].ID)
		gone, _ := forget(cfg.Connections[n-1].ID)
		if told {
			fmt.Printf("Forgot %s, and Studio was told.\n", gone.Folder)
		} else {
			fmt.Printf("Forgot %s. Studio could not be reached: disconnect it there too (Open folder → My computers).\n", gone.Folder)
		}
	case "version", "-v", "--version":
		fmt.Println("studio-helper", version)
	case "run":
		// Every saved connection, in this console, without the tray or the form.
		cfg := loadConfig()
		fmt.Printf("Sharing %d folder(s). Close this to stop.\n", len(cfg.Connections))
		for _, c := range cfg.Connections {
			manager.start(c)
		}
		manager.wait()
	default:
		fmt.Println("studio-helper " + version + `: connects folders on this computer to Eidoloom Studio.

  studio-helper            the helper in the tray, with its form in the browser
  studio-helper --background   the same, without opening the form (used when signing in)
  studio-helper run        every saved connection in this console, no tray
  studio-helper pair       pair a folder by answering questions here
  studio-helper list       show saved connections
  studio-helper remove N   forget connection N
  studio-helper pair -server URL -code CODE -folder PATH [-trust]`)
	}
}
