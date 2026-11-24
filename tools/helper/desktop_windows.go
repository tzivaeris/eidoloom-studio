//go:build windows

package main

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"

	"golang.org/x/sys/windows/registry"
)

func hidden(cmd *exec.Cmd) *exec.Cmd {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000} // CREATE_NO_WINDOW
	return cmd
}

func openURL(u string) error { return exec.Command("rundll32", "url.dll,FileProtocolHandler", u).Start() }

// showForm opens the helper's own window: Windows Forms through PowerShell, with no console, talking to the form API.
func showForm(info uiInfo, focus string) error {
	dir := filepath.Join(os.TempDir(), "strata-studio-helper")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	script, _ := files.ReadFile("form.ps1")
	ps1, ico := filepath.Join(dir, "window.ps1"), filepath.Join(dir, "icon.ico")
	if err := os.WriteFile(ps1, append([]byte{0xEF, 0xBB, 0xBF}, script...), 0o600); err != nil {
		return err
	}
	icon, _ := files.ReadFile("assets/on.ico")
	_ = os.WriteFile(ico, icon, 0o600)
	args := []string{"-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", ps1,
		"-Port", strconv.Itoa(info.Port), "-Token", info.Token, "-IconPath", ico, "-Version", version, "-Settings", configDir()}
	if focus != "" {
		args = append(args, "-Focus", focus)
	}
	// No console at all (CREATE_NO_WINDOW), but no "hidden" start either: Windows applies a hidden start to the first
	// window the program shows, which in 0.03 made the helper's own window invisible.
	cmd := exec.Command("powershell.exe", args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{CreationFlags: 0x08000000}
	var out tailBuffer
	cmd.Stdout, cmd.Stderr = &out, &out
	if err := cmd.Start(); err != nil {
		logf("helper", "cannot start the window: %v", err)
		return err
	}
	go func() {
		if err := cmd.Wait(); err != nil {
			logf("helper", "the window closed with an error: %v %s", err, oneLine(strings.TrimSpace(out.String())))
		}
	}()
	return nil
}

// closeOldWindows ends helper windows left by an earlier run: 0.03 could leave invisible ones behind.
func closeOldWindows() {
	script := "Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | Where-Object { $_.CommandLine -like '*strata-studio-helper*window.ps1*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
	_ = hidden(exec.Command("powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script)).Run()
}

func openPath(p string) error { return exec.Command("explorer.exe", p).Start() }

func canPickFolder() bool { return true }

// pickFolder shows Windows' own folder dialog, in front of the browser, through PowerShell.
func pickFolder() (string, error) {
	script := "Add-Type -AssemblyName System.Windows.Forms; [Console]::OutputEncoding=[Text.Encoding]::UTF8; " +
		"$owner = New-Object System.Windows.Forms.Form -Property @{TopMost=$true; ShowInTaskbar=$false}; " +
		"$d = New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description = 'Choose the folder to share with Eidoloom Studio'; $d.ShowNewFolderButton = $true; " +
		"if ($d.ShowDialog($owner) -eq 'OK') { $d.SelectedPath }"
	out, err := hidden(exec.Command("powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", script)).Output()
	path := strings.TrimSpace(string(out))
	if err != nil || path == "" {
		return "", errors.New("no folder chosen")
	}
	return path, nil
}

const runKey = `Software\Microsoft\Windows\CurrentVersion\Run`
const runName = "StrataStudioHelper"

func autostartEnabled() bool {
	k, err := registry.OpenKey(registry.CURRENT_USER, runKey, registry.QUERY_VALUE)
	if err != nil {
		return false
	}
	defer k.Close()
	_, _, err = k.GetStringValue(runName)
	return err == nil
}

func setAutostart(on bool) error {
	k, _, err := registry.CreateKey(registry.CURRENT_USER, runKey, registry.SET_VALUE|registry.QUERY_VALUE)
	if err != nil {
		return err
	}
	defer k.Close()
	if !on {
		if err := k.DeleteValue(runName); err != nil && err != registry.ErrNotExist {
			return err
		}
		return nil
	}
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	return k.SetStringValue(runName, `"`+exe+`" --background`)
}
