//go:build !windows

package main

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

func openURL(u string) error { return exec.Command("xdg-open", u).Start() }

func closeOldWindows() {}

// showForm opens the helper's page in the browser; Windows has its own window instead.
func showForm(info uiInfo, focus string) error {
	if focus != "" {
		return openURL(info.url() + "#" + focus)
	}
	return openURL(info.url())
}

func openPath(p string) error { return exec.Command("xdg-open", p).Start() }

func canPickFolder() bool {
	_, z := exec.LookPath("zenity")
	_, k := exec.LookPath("kdialog")
	return z == nil || k == nil
}

func pickFolder() (string, error) {
	var out []byte
	var err error
	if _, e := exec.LookPath("zenity"); e == nil {
		out, err = exec.Command("zenity", "--file-selection", "--directory", "--title=Choose the folder to share with Eidoloom Studio").Output()
	} else if _, e := exec.LookPath("kdialog"); e == nil {
		out, err = exec.Command("kdialog", "--getexistingdirectory", os.Getenv("HOME"), "--title", "Choose the folder to share with Eidoloom Studio").Output()
	} else {
		return "", errors.New("no folder picker here; type the folder's path")
	}
	if err != nil {
		return "", errors.New("no folder chosen")
	}
	return strings.TrimSpace(string(out)), nil
}

func autostartFile() string {
	base := os.Getenv("XDG_CONFIG_HOME")
	if base == "" {
		home, _ := os.UserHomeDir()
		base = filepath.Join(home, ".config")
	}
	return filepath.Join(base, "autostart", "strata-studio-helper.desktop")
}

func autostartEnabled() bool {
	_, err := os.Stat(autostartFile())
	return err == nil
}

func setAutostart(on bool) error {
	if !on {
		err := os.Remove(autostartFile())
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(autostartFile()), 0o755); err != nil {
		return err
	}
	entry := "[Desktop Entry]\nType=Application\nName=Eidoloom Studio helper\nComment=Shares folders with Eidoloom Studio\n" +
		"Exec=\"" + exe + "\" --background\nTerminal=false\nX-GNOME-Autostart-enabled=true\n"
	return writeAtomic(autostartFile(), []byte(entry), 0o644)
}
