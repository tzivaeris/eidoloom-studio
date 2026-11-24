package main

// The helper as people see it: an icon in the tray, and a small form in the browser that only this computer can open.

import (
	"context"
	"crypto/rand"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

//go:embed ui.html form.ps1 assets/*
var files embed.FS

// ---- the running connections

type Live struct {
	mu       sync.Mutex
	conn     Connection
	state    string // connecting, connected, offline, stopped
	detail   string
	files    int
	skipped  []string
	lastSync time.Time
	cancel   context.CancelFunc
}

func (l *Live) set(state, detail string) {
	l.mu.Lock()
	changed := l.state != state
	l.state, l.detail = state, detail
	l.mu.Unlock()
	if changed {
		manager.changed()
	}
}

func (l *Live) status() string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.state
}

func (l *Live) synced(files int, skipped []string) {
	l.mu.Lock()
	l.files, l.skipped, l.lastSync = files, append([]string(nil), skipped...), time.Now()
	l.mu.Unlock()
}

type Manager struct {
	mu       sync.Mutex
	live     map[string]*Live
	wg       sync.WaitGroup
	onChange func()
}

var manager = &Manager{live: map[string]*Live{}}

func (m *Manager) start(c Connection) {
	ctx, cancel := context.WithCancel(context.Background())
	l := &Live{conn: c, state: "connecting", cancel: cancel}
	m.mu.Lock()
	if old := m.live[c.ID]; old != nil {
		old.cancel()
	}
	m.live[c.ID] = l
	m.mu.Unlock()
	m.wg.Add(1)
	go func() {
		defer m.wg.Done()
		serve(ctx, c, l)
	}()
	m.changed()
}

func (m *Manager) stop(id string) {
	m.mu.Lock()
	if l := m.live[id]; l != nil {
		l.cancel()
		delete(m.live, id)
	}
	m.mu.Unlock()
	m.changed()
}

func (m *Manager) wait() { m.wg.Wait() }

func (m *Manager) changed() {
	if m.onChange != nil {
		go m.onChange()
	}
}

type liveView struct {
	ID       string   `json:"id"`
	Name     string   `json:"name"`
	Folder   string   `json:"folder"`
	Server   string   `json:"server"`
	State    string   `json:"state"`
	Detail   string   `json:"detail"`
	Files    int      `json:"files"`
	Skipped  []string `json:"skipped"`
	LastSync string   `json:"last_sync"`
}

func (m *Manager) views() []liveView {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []liveView{}
	for _, l := range m.live {
		l.mu.Lock()
		v := liveView{ID: l.conn.ID, Name: l.conn.Name, Folder: l.conn.Folder, Server: l.conn.Server, State: l.state, Detail: l.detail, Files: l.files, Skipped: l.skipped}
		if !l.lastSync.IsZero() {
			v.LastSync = l.lastSync.Format("15:04:05")
		}
		l.mu.Unlock()
		out = append(out, v)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// overall is what the tray icon shows: on when every folder is connected, wait while any is connecting, else off.
func (m *Manager) overall() (string, string) {
	views := m.views()
	if len(views) == 0 {
		return "off", "No folder is connected yet"
	}
	on, waiting := 0, 0
	for _, v := range views {
		switch v.State {
		case "connected":
			on++
		case "connecting":
			waiting++
		}
	}
	text := fmt.Sprintf("%d of %d folder(s) connected", on, len(views))
	switch {
	case on == len(views):
		return "on", text
	case waiting > 0:
		return "wait", text
	}
	return "off", text
}

func (r *Runner) closeAll() {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, c := range r.cancel {
		c()
	}
	for _, t := range r.terms {
		t.Close()
	}
}

// ---- the form

type uiInfo struct {
	Port    int    `json:"port"`
	Token   string `json:"token"`
	Pid     int    `json:"pid"`
	Version string `json:"version"` // 0.07 on; an older running helper has none
}

// olderThan says whether helper version a is older than b ("" counts as older than any).
func olderThan(a, b string) bool {
	if a == "" {
		return b != ""
	}
	pa, pb := strings.Split(a, "."), strings.Split(b, ".")
	for i := 0; i < len(pa) || i < len(pb); i++ {
		var x, y int
		if i < len(pa) {
			x, _ = strconv.Atoi(pa[i])
		}
		if i < len(pb) {
			y, _ = strconv.Atoi(pb[i])
		}
		if x != y {
			return x < y
		}
	}
	return false
}

// replaceOlder asks an older helper that is running to quit, so this newer one takes over its folders (0.07): before,
// starting a new version only opened the old one's window, and the folders went on with the old version.
func replaceOlder(info uiInfo) bool {
	if !olderThan(info.Version, version) {
		return false
	}
	client := &http.Client{Timeout: 3 * time.Second}
	req, _ := http.NewRequest("POST", fmt.Sprintf("http://127.0.0.1:%d/api/quit", info.Port), strings.NewReader("{}"))
	req.Header.Set("X-Token", info.Token)
	req.Header.Set("Content-Type", "application/json")
	if resp, err := client.Do(req); err == nil {
		resp.Body.Close()
	}
	for i := 0; i < 40; i++ { // up to 10 s for it to let go
		time.Sleep(250 * time.Millisecond)
		if _, ok := runningUI(); !ok {
			return true
		}
	}
	return true
}

func uiInfoPath() string { return filepath.Join(configDir(), "ui.json") }

func (u uiInfo) url() string { return fmt.Sprintf("http://127.0.0.1:%d/?t=%s", u.Port, u.Token) }

// runningUI answers the form of a helper already running for this person, if there is one.
func runningUI() (uiInfo, bool) {
	var info uiInfo
	data, err := os.ReadFile(uiInfoPath())
	if err != nil || json.Unmarshal(data, &info) != nil || info.Port == 0 {
		return info, false
	}
	client := &http.Client{Timeout: 2 * time.Second}
	req, _ := http.NewRequest("GET", fmt.Sprintf("http://127.0.0.1:%d/api/ping", info.Port), nil)
	req.Header.Set("X-Token", info.Token)
	resp, err := client.Do(req)
	if err != nil {
		return info, false
	}
	resp.Body.Close()
	return info, resp.StatusCode == 200
}

func startUI() (uiInfo, error) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return uiInfo{}, err
	}
	buf := make([]byte, 24)
	_, _ = rand.Read(buf)
	info := uiInfo{Port: ln.Addr().(*net.TCPAddr).Port, Token: hex.EncodeToString(buf), Pid: os.Getpid(), Version: version}
	_ = os.MkdirAll(configDir(), 0o700)
	data, _ := json.Marshal(info)
	if err := writeAtomic(uiInfoPath(), data, 0o600); err != nil {
		return uiInfo{}, err
	}
	mux := http.NewServeMux()
	page, _ := files.ReadFile("ui.html")
	icon, _ := files.ReadFile("assets/on.png")
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
		w.Header().Set("Referrer-Policy", "no-referrer")
		_, _ = w.Write(page)
	})
	mux.HandleFunc("/icon.png", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "image/png")
		_, _ = w.Write(icon)
	})
	api := func(path string, fn func(r *http.Request) (any, error)) {
		mux.HandleFunc(path, func(w http.ResponseWriter, r *http.Request) {
			// Only this page, from this computer: the token in its address, the right Host (no DNS rebinding) and no other site's Origin.
			host := r.Host
			if host != fmt.Sprintf("127.0.0.1:%d", info.Port) || r.Header.Get("X-Token") != info.Token ||
				(r.Header.Get("Origin") != "" && r.Header.Get("Origin") != "http://"+host) {
				http.Error(w, `{"error":"not allowed"}`, http.StatusForbidden)
				return
			}
			out, err := fn(r)
			w.Header().Set("Content-Type", "application/json; charset=utf-8") // Windows PowerShell reads anything else as Latin-1
			if err != nil {
				w.WriteHeader(http.StatusBadRequest)
				_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
				return
			}
			_ = json.NewEncoder(w).Encode(out)
		})
	}
	body := func(r *http.Request, into any) error {
		if r.Method != "POST" {
			return errors.New("POST expected")
		}
		return json.NewDecoder(http.MaxBytesReader(nil, r.Body, 1<<16)).Decode(into)
	}
	api("/api/ping", func(r *http.Request) (any, error) { return map[string]bool{"ok": true}, nil })
	api("/api/state", func(r *http.Request) (any, error) {
		last := "https://studio.local"
		if cs := loadConfig().Connections; len(cs) > 0 {
			last = cs[len(cs)-1].Server
		}
		home, _ := os.UserHomeDir()
		return map[string]any{"folders": manager.views(), "autostart": autostartEnabled(), "log": recentLog(), "version": version,
			"last_server": last, "home": home, "can_browse": canPickFolder(), "os": osName()}, nil
	})
	api("/api/pair", func(r *http.Request) (any, error) {
		var in struct{ Server, Code, Folder, Trust string }
		if err := body(r, &in); err != nil {
			return nil, err
		}
		server := strings.TrimRight(strings.TrimSpace(in.Server), "/")
		if !strings.Contains(server, "://") {
			server = "https://" + server
		}
		pin, untrusted, err := certificateCheck(server)
		if err != nil {
			return nil, err
		}
		if untrusted && in.Trust != pin {
			return map[string]any{"need_trust": true, "fingerprint": groups(pin), "pin": pin, "server": server}, nil
		}
		c, err := register(server, in.Code, in.Folder, pin)
		if err != nil {
			return nil, err
		}
		logf(c.Name, "paired: %s is \"%s\" in Studio", c.Folder, c.Name)
		manager.start(c)
		cfg := loadConfig()
		if !cfg.AutostartSet {
			if err := setAutostart(true); err == nil {
				cfg.AutostartSet = true
				_ = saveConfig(cfg)
			}
		}
		return map[string]any{"ok": true, "name": c.Name}, nil
	})
	api("/api/remove", func(r *http.Request) (any, error) {
		var in struct{ ID string }
		if err := body(r, &in); err != nil {
			return nil, err
		}
		told := tellStudioGone(in.ID)
		manager.stop(in.ID)
		c, ok := forget(in.ID)
		if !ok {
			return nil, errors.New("that folder is not connected")
		}
		if told {
			logf(c.Name, "stopped sharing %s; Studio was told", c.Folder)
		} else {
			logf(c.Name, "stopped sharing %s; Studio could not be reached, so disconnect it there too", c.Folder)
		}
		return map[string]bool{"ok": true, "studio_told": told}, nil
	})
	api("/api/reconnect", func(r *http.Request) (any, error) {
		var in struct{ ID string }
		if err := body(r, &in); err != nil {
			return nil, err
		}
		for _, c := range loadConfig().Connections {
			if c.ID == in.ID {
				manager.start(c)
				return map[string]bool{"ok": true}, nil
			}
		}
		return nil, errors.New("that folder is not connected")
	})
	api("/api/browse", func(r *http.Request) (any, error) {
		if r.Method != "POST" {
			return nil, errors.New("POST expected")
		}
		path, err := pickFolder()
		if err != nil {
			return nil, err
		}
		return map[string]string{"path": path}, nil
	})
	api("/api/open", func(r *http.Request) (any, error) {
		var in struct{ ID string }
		if err := body(r, &in); err != nil {
			return nil, err
		}
		for _, c := range loadConfig().Connections {
			if c.ID == in.ID {
				return map[string]bool{"ok": true}, openPath(c.Folder)
			}
		}
		return nil, errors.New("that folder is not connected")
	})
	api("/api/autostart", func(r *http.Request) (any, error) {
		var in struct{ On bool }
		if err := body(r, &in); err != nil {
			return nil, err
		}
		cfg := loadConfig()
		cfg.AutostartSet = true
		_ = saveConfig(cfg)
		return map[string]bool{"on": in.On}, setAutostart(in.On)
	})
	api("/api/quit", func(r *http.Request) (any, error) {
		go func() { time.Sleep(300 * time.Millisecond); quitApp() }()
		return map[string]bool{"ok": true}, nil
	})
	go func() { _ = http.Serve(ln, mux) }()
	return info, nil
}

// runApp is the helper as most people run it: no window, an icon in the tray, the form when wanted.
func runApp(background, noTray bool) {
	if info, ok := runningUI(); ok && replaceOlder(info) {
		logf("helper", "replaced the older helper %s that was running", info.Version)
	} else if ok {
		if !background && os.Getenv("STUDIO_HELPER_NO_BROWSER") == "" {
			_ = showForm(info, "") // already running: starting it again shows its window, and leaves
		}
		return
	}
	closeOldWindows()
	info, err := startUI()
	if err != nil {
		logf("helper", "cannot start the form: %v", err)
		os.Exit(1)
	}
	cfg := loadConfig()
	logf("helper", "started %s; form at http://127.0.0.1:%d", version, info.Port)
	if autostartEnabled() {
		_ = setAutostart(true) // sign-in starts this copy from now on, not an older file somewhere else
	}
	for _, c := range cfg.Connections {
		manager.start(c)
	}
	// Started by hand, it shows its window; started at sign-in (--background), only the tray icon appears.
	if !background && os.Getenv("STUDIO_HELPER_NO_BROWSER") == "" {
		focus := ""
		if len(cfg.Connections) == 0 {
			focus = "connect"
		}
		_ = showForm(info, focus)
	}
	if noTray || !trayAvailable() {
		select {} // no tray here: the form is the way in (run studio-helper again to open it)
	}
	runTray(info)
}
