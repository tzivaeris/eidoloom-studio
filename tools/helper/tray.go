package main

import (
	"os"
	"runtime"

	"fyne.io/systray"
)

func trayAvailable() bool {
	// Linux shows tray icons through the desktop's session bus; without one (a server, a plain console) there is no tray.
	return runtime.GOOS == "windows" || os.Getenv("DBUS_SESSION_BUS_ADDRESS") != ""
}

func trayIcon(state string) []byte {
	ext := ".png"
	if runtime.GOOS == "windows" {
		ext = ".ico"
	}
	data, _ := files.ReadFile("assets/" + state + ext)
	return data
}

var trayQuit = func() {}

func quitApp() {
	trayQuit()
	os.Exit(0)
}

func runTray(info uiInfo) {
	systray.Run(func() {
		trayQuit = systray.Quit
		systray.SetIcon(trayIcon("wait"))
		systray.SetTitle("")
		systray.SetTooltip("Eidoloom Studio helper")
		name := systray.AddMenuItem("Eidoloom Studio helper "+version, "")
		name.Disable()
		status := systray.AddMenuItem("", "")
		status.Disable()
		systray.AddSeparator()
		open := systray.AddMenuItem("Open the helper", "See the connected folders and their activity")
		connect := systray.AddMenuItem("Connect a folder…", "Share another folder with Studio")
		systray.AddSeparator()
		auto := systray.AddMenuItemCheckbox("Start when I sign in", "Run the helper by itself when you sign in to this computer", autostartEnabled())
		quit := systray.AddMenuItem("Quit", "Stop sharing every folder until the helper is started again")
		update := func() {
			state, text := manager.overall()
			systray.SetIcon(trayIcon(state))
			systray.SetTooltip("Eidoloom Studio helper " + version + ": " + text)
			status.SetTitle(text)
			if autostartEnabled() {
				auto.Check()
			} else {
				auto.Uncheck()
			}
		}
		manager.onChange = update
		update()
		go func() {
			for {
				select {
				case <-open.ClickedCh:
					_ = showForm(info, "")
				case <-connect.ClickedCh:
					_ = showForm(info, "connect")
				case <-auto.ClickedCh:
					cfg := loadConfig()
					cfg.AutostartSet = true
					_ = saveConfig(cfg)
					_ = setAutostart(!autostartEnabled())
					update()
				case <-quit.ClickedCh:
					systray.Quit()
					return
				}
			}
		}()
	}, func() { os.Exit(0) })
}
