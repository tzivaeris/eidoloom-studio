//go:build windows

package main

import (
	"context"
	"os"
	"os/exec"
	"strconv"
	"syscall"

	"github.com/UserExistsError/conpty"
)

func shellName() string { return "powershell" }

// PowerShell writes UTF-8 so Greek and other text arrive intact, and a failed native program or cmdlet gives a
// non-zero exit code, as bash would.
const psWrap = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; " +
	"$global:LASTEXITCODE=0; & { %s }; if ($global:LASTEXITCODE) { exit $global:LASTEXITCODE } elseif (-not $?) { exit 1 }"

func shellCommand(job Job) *exec.Cmd {
	cmd := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", sprintf(psWrap, job.Command))
	return cmd
}

func sprintf(format, value string) string {
	out := []byte{}
	for i := 0; i < len(format); i++ {
		if format[i] == '%' && i+1 < len(format) && format[i+1] == 's' {
			out = append(out, value...)
			i++
			continue
		}
		out = append(out, format[i])
	}
	return string(out)
}

func prepare(cmd *exec.Cmd) {
	// No console window flashes up for each command, since the helper itself has none.
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x00000200 | 0x08000000} // CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW
}

func killTree(cmd *exec.Cmd) {
	if cmd.Process != nil {
		_ = hidden(exec.Command("taskkill", "/T", "/F", "/PID", strconv.Itoa(cmd.Process.Pid))).Run()
	}
}

type Pty interface {
	Read([]byte) (int, error)
	Write([]byte) (int, error)
	Resize(cols, rows int) error
	Close()
	Wait() int
	Pid() int
}

type winPty struct{ c *conpty.ConPty }

func startPty(dir string, cols, rows int) (Pty, error) {
	c, err := conpty.Start("powershell.exe -NoLogo", conpty.ConPtyDimensions(cols, rows), conpty.ConPtyWorkDir(dir), conpty.ConPtyEnv(os.Environ()))
	if err != nil {
		return nil, err
	}
	return &winPty{c: c}, nil
}

func (p *winPty) Read(b []byte) (int, error)  { return p.c.Read(b) }
func (p *winPty) Write(b []byte) (int, error) { return p.c.Write(b) }
func (p *winPty) Resize(cols, rows int) error { return p.c.Resize(cols, rows) }
func (p *winPty) Close()                      { _ = p.c.Close() }
func (p *winPty) Pid() int                    { return p.c.Pid() }
func (p *winPty) Wait() int {
	code, err := p.c.Wait(context.Background())
	if err != nil {
		return -1
	}
	return int(code)
}
