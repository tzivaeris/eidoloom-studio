# The helper's window on Windows: Windows Forms, talking to the helper on 127.0.0.1 with the token it was started with.
# Kept to plain ASCII and Windows PowerShell 5.1, which every Windows 10 and 11 has.
param([int]$Port, [string]$Token, [string]$Focus = '', [string]$IconPath = '', [string]$Version = '', [string]$Settings = '')

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName Microsoft.VisualBasic

# Anything that goes wrong is written beside the helper's log and shown, instead of the window silently not appearing.
$logPath = Join-Path $Settings 'window.log'
trap {
    try { Add-Content -Path $logPath -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $_.ToString() + ' ' + $_.InvocationInfo.PositionMessage) } catch { }
    try { [System.Windows.Forms.MessageBox]::Show("The helper's window failed:`r`n`r`n" + $_.ToString(), 'Eidoloom Studio helper') | Out-Null } catch { }
    exit 1
}

# One window at a time for this run of the helper: a second start brings the first to the front.
$created = $false
$mutex = [System.Threading.Mutex]::new($true, ('StrataStudioHelperWindow-' + $Port), [ref]$created)
if (-not $created) {
    try { [Microsoft.VisualBasic.Interaction]::AppActivate('Eidoloom Studio helper ' + $Version) } catch { }
    exit 0
}

# The taskbar groups windows by the program's identity, so this window would sit under PowerShell's icon. An identity
# of its own, set before any window exists, makes the taskbar show the window's icon: the helper's.
try {
    Add-Type -Namespace StrataStudio -Name Shell -MemberDefinition '[System.Runtime.InteropServices.DllImport("shell32.dll")] public static extern int SetCurrentProcessExplicitAppUserModelID([System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.LPWStr)] string AppID);'
    [void][StrataStudio.Shell]::SetCurrentProcessExplicitAppUserModelID('StrataStudio.Helper')
} catch { }

[System.Windows.Forms.Application]::EnableVisualStyles()
[System.Net.WebRequest]::DefaultWebProxy = New-Object System.Net.WebProxy   # the helper is on this computer: never through a proxy
$base = "http://127.0.0.1:$Port"

function Get-ErrorText($err) {
    if ($err.ErrorDetails -and $err.ErrorDetails.Message) {
        try { $parsed = $err.ErrorDetails.Message | ConvertFrom-Json; if ($parsed.error) { return [string]$parsed.error } } catch { }
        return [string]$err.ErrorDetails.Message
    }
    return [string]$err.Exception.Message
}

function Invoke-Helper([string]$Path, $Body = $null, [int]$Timeout = 15) {
    $headers = @{ 'X-Token' = $Token }
    try {
        if ($null -ne $Body) {
            $bytes = [System.Text.Encoding]::UTF8.GetBytes(($Body | ConvertTo-Json -Compress))
            return Invoke-RestMethod -Uri ($base + $Path) -Method Post -Headers $headers -ContentType 'application/json; charset=utf-8' -Body $bytes -TimeoutSec $Timeout
        }
        return Invoke-RestMethod -Uri ($base + $Path) -Headers $headers -TimeoutSec $Timeout
    } catch {
        throw (Get-ErrorText $_)
    }
}

function Upper-First([string]$text) {
    if (-not $text) { return '' }
    return $text.Substring(0, 1).ToUpper() + $text.Substring(1)
}

# ---- the window
$font = New-Object System.Drawing.Font('Segoe UI', 9)
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Eidoloom Studio helper ' + $Version
$form.Font = $font
$form.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::Font
$form.ClientSize = New-Object System.Drawing.Size(640, 600)
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedSingle
$form.MaximizeBox = $false
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
if ($IconPath -and (Test-Path $IconPath)) { try { $form.Icon = New-Object System.Drawing.Icon($IconPath) } catch { } }

function New-Control($type, [int]$x, [int]$y, [int]$w, [int]$h, [string]$text = '') {
    $c = New-Object $type
    $c.Location = New-Object System.Drawing.Point($x, $y)
    $c.Size = New-Object System.Drawing.Size($w, $h)
    if ($text) { $c.Text = $text }
    return $c
}

$title = New-Control System.Windows.Forms.Label 16 12 600 28 ('Eidoloom Studio helper ' + $Version)
$title.Font = New-Object System.Drawing.Font('Segoe UI', 14, [System.Drawing.FontStyle]::Bold)
$summary = New-Control System.Windows.Forms.Label 18 42 600 20 'Starting...'
$summary.ForeColor = [System.Drawing.Color]::DimGray
$form.Controls.AddRange(@($title, $summary))

# Your folders
$foldersBox = New-Control System.Windows.Forms.GroupBox 12 68 616 200 'Your folders'
$list = New-Control System.Windows.Forms.ListView 12 22 592 130
$list.View = [System.Windows.Forms.View]::Details
$list.FullRowSelect = $true
$list.MultiSelect = $false
$list.HideSelection = $false
[void]$list.Columns.Add('In Studio', 150)
[void]$list.Columns.Add('On this computer', 220)
[void]$list.Columns.Add('State', 150)
[void]$list.Columns.Add('Files', 60)
$openBtn = New-Control System.Windows.Forms.Button 12 160 110 28 'Open folder'
$retryBtn = New-Control System.Windows.Forms.Button 128 160 100 28 'Try again'
$removeBtn = New-Control System.Windows.Forms.Button 494 160 110 28 'Disconnect'
$foldersBox.Controls.AddRange(@($list, $openBtn, $retryBtn, $removeBtn))
$form.Controls.Add($foldersBox)

# Connect a folder
$connectBox = New-Control System.Windows.Forms.GroupBox 12 278 616 236 'Connect a folder'
$hint = New-Control System.Windows.Forms.Label 12 22 592 32 'In Studio choose Open folder, then Connect a folder on my computer. It shows a pairing code; enter it here within 10 minutes.'
$hint.ForeColor = [System.Drawing.Color]::DimGray
$serverLabel = New-Control System.Windows.Forms.Label 12 64 130 20 "Studio's address"
$serverBox = New-Control System.Windows.Forms.TextBox 150 61 454 24
$codeLabel = New-Control System.Windows.Forms.Label 12 98 130 20 'Pairing code'
$codeBox = New-Control System.Windows.Forms.TextBox 150 94 150 28
$codeBox.Font = New-Object System.Drawing.Font('Consolas', 13, [System.Drawing.FontStyle]::Bold)
$codeBox.CharacterCasing = [System.Windows.Forms.CharacterCasing]::Upper
$codeBox.MaxLength = 9
$folderLabel = New-Control System.Windows.Forms.Label 12 136 130 20 'Folder to share'
$folderBox = New-Control System.Windows.Forms.TextBox 150 133 360 24
$browseBtn = New-Control System.Windows.Forms.Button 516 131 88 28 'Browse...'
$connectBtn = New-Control System.Windows.Forms.Button 150 170 110 30 'Connect'
$connectBtn.Font = New-Object System.Drawing.Font('Segoe UI', 9, [System.Drawing.FontStyle]::Bold)
$message = New-Control System.Windows.Forms.Label 270 168 334 60 ''
$connectBox.Controls.AddRange(@($hint, $serverLabel, $serverBox, $codeLabel, $codeBox, $folderLabel, $folderBox, $browseBtn, $connectBtn, $message))
$form.Controls.Add($connectBox)
$form.AcceptButton = $connectBtn

# Settings
$autoBox = New-Control System.Windows.Forms.CheckBox 18 528 300 24 'Start the helper when I sign in to this computer'
$aboutBtn = New-Control System.Windows.Forms.Button 322 526 80 28 'About'
$activityBtn = New-Control System.Windows.Forms.Button 408 526 90 28 'Activity...'
$quitBtn = New-Control System.Windows.Forms.Button 504 526 124 28 'Quit the helper'
$note = New-Control System.Windows.Forms.Label 18 562 610 32 'The helper keeps running in the tray, by the clock, when this window is closed. Quitting stops sharing every folder until you start it again.'
$note.ForeColor = [System.Drawing.Color]::DimGray
$form.Controls.AddRange(@($autoBox, $aboutBtn, $activityBtn, $quitBtn, $note))

function Show-Message([string]$text, [bool]$good = $false) {
    $message.Text = (Upper-First $text)
    if ($good) { $message.ForeColor = [System.Drawing.Color]::ForestGreen } else { $message.ForeColor = [System.Drawing.Color]::Firebrick }
}

$script:lastFolders = ''
$script:lastLog = @()
$script:filled = $false
$script:settingAuto = $false
$script:missed = 0
$stateNames = @{ connected = 'Connected'; connecting = 'Connecting...'; offline = 'Offline'; stopped = 'Stopped' }

function Selected-Id {
    if ($list.SelectedItems.Count -gt 0) { return [string]$list.SelectedItems[0].Tag }
    return $null
}

function Update-Buttons {
    $has = $list.SelectedItems.Count -gt 0
    $openBtn.Enabled = $has
    $removeBtn.Enabled = $has
    $retryBtn.Enabled = $has -and ($list.SelectedItems[0].SubItems[2].Text -notlike 'Connected*')
}

function Refresh-State {
    try { $s = Invoke-Helper '/api/state' -Timeout 5 } catch {
        $summary.Text = 'The helper is not running. Start it again to share your folders.'
        $connectBtn.Enabled = $false
        $script:missed++
        if ($script:missed -ge 5) { $form.Close() }      # its helper has quit: this window belongs to that run only
        return
    }
    $script:missed = 0
    $connectBtn.Enabled = $true
    $folders = @($s.folders)
    $on = @($folders | Where-Object { $_.state -eq 'connected' }).Count
    if ($folders.Count -gt 0) { $summary.Text = "$on of $($folders.Count) folder(s) connected   -   version $($s.version)" }
    else { $summary.Text = "No folder is shared yet   -   version $($s.version)" }
    $key = ($folders | ConvertTo-Json -Compress -Depth 4)
    if ($key -ne $script:lastFolders) {
        $script:lastFolders = $key
        $keep = Selected-Id
        $list.BeginUpdate()
        $list.Items.Clear()
        foreach ($f in $folders) {
            $state = $stateNames[[string]$f.state]
            if (-not $state) { $state = [string]$f.state }
            if ($f.detail) { $state = "$state - $($f.detail)" }
            $item = New-Object System.Windows.Forms.ListViewItem([string]$f.name)
            [void]$item.SubItems.Add([string]$f.folder)
            [void]$item.SubItems.Add($state)
            [void]$item.SubItems.Add([string]$f.files)
            $item.Tag = [string]$f.id
            $item.ToolTipText = "$($f.folder) -> $($f.server)"
            if ($f.state -eq 'connected') { $item.ForeColor = [System.Drawing.Color]::DarkGreen }
            elseif ($f.state -ne 'connecting') { $item.ForeColor = [System.Drawing.Color]::Firebrick }
            [void]$list.Items.Add($item)
            if ($item.Tag -eq $keep) { $item.Selected = $true }
        }
        if ($list.SelectedItems.Count -eq 0 -and $list.Items.Count -gt 0) { $list.Items[0].Selected = $true }
        $list.EndUpdate()
        Update-Buttons
    }
    $script:lastLog = @($s.log)
    if (-not $script:filled) {
        $script:filled = $true
        $serverBox.Text = [string]$s.last_server
        if ($Focus -eq 'connect' -or $folders.Count -eq 0) { $codeBox.Select() }
    }
    if ($autoBox.Checked -ne [bool]$s.autostart) { $script:settingAuto = $true; $autoBox.Checked = [bool]$s.autostart; $script:settingAuto = $false }
}

$list.Add_SelectedIndexChanged({ Update-Buttons })

$codeBox.Add_TextChanged({
    $raw = ($codeBox.Text.ToUpper() -replace '[^A-Z0-9]', '')
    if ($raw.Length -gt 8) { $raw = $raw.Substring(0, 8) }
    if ($raw.Length -gt 4) { $tidy = $raw.Substring(0, 4) + '-' + $raw.Substring(4) } else { $tidy = $raw }
    if ($codeBox.Text -ne $tidy) { $codeBox.Text = $tidy; $codeBox.SelectionStart = $tidy.Length }
})

$browseBtn.Add_Click({
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Choose the folder to share with Eidoloom Studio'
    $dialog.ShowNewFolderButton = $true
    if ($folderBox.Text -and (Test-Path $folderBox.Text)) { $dialog.SelectedPath = $folderBox.Text }
    if ($dialog.ShowDialog($form) -eq [System.Windows.Forms.DialogResult]::OK) { $folderBox.Text = $dialog.SelectedPath }
})

function Connect-Folder([string]$trust) {
    $connectBtn.Enabled = $false
    Show-Message 'Connecting...' $true
    $form.Cursor = [System.Windows.Forms.Cursors]::WaitCursor
    [System.Windows.Forms.Application]::DoEvents()
    try {
        $r = Invoke-Helper '/api/pair' @{ server = $serverBox.Text; code = $codeBox.Text; folder = $folderBox.Text; trust = $trust } -Timeout 120
        if ($r.need_trust) {
            $text = "This computer does not know the certificate of $($r.server).`r`n`r`nIts fingerprint:`r`n$($r.fingerprint)`r`n`r`nTrust it only if this is the address of your own Studio, on your own network. Trust it and connect?"
            $answer = [System.Windows.Forms.MessageBox]::Show($form, $text, 'Eidoloom Studio helper', [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Warning)
            if ($answer -eq [System.Windows.Forms.DialogResult]::Yes) { Connect-Folder ([string]$r.pin); return }
            Show-Message 'Not connected: the certificate was not trusted.'
            return
        }
        $codeBox.Text = ''
        $folderBox.Text = ''
        Show-Message "Connected: it is `"$($r.name)`" in Studio. Open it there from Open folder, My computers." $true
        Refresh-State
    } catch {
        Show-Message ([string]$_.Exception.Message)
    } finally {
        $form.Cursor = [System.Windows.Forms.Cursors]::Default
        $connectBtn.Enabled = $true
    }
}

$connectBtn.Add_Click({
    if (-not $serverBox.Text.Trim()) { Show-Message "Enter Studio's address."; $serverBox.Select(); return }
    if (($codeBox.Text -replace '[^A-Z0-9]', '').Length -ne 8) { Show-Message 'Enter the 8-character code that Studio shows.'; $codeBox.Select(); return }
    if (-not $folderBox.Text.Trim()) { Show-Message 'Choose the folder to share.'; $browseBtn.Select(); return }
    Connect-Folder ''
})

$openBtn.Add_Click({ $id = Selected-Id; if ($id) { try { [void](Invoke-Helper '/api/open' @{ id = $id }) } catch { Show-Message ([string]$_.Exception.Message) } } })
$retryBtn.Add_Click({ $id = Selected-Id; if ($id) { try { [void](Invoke-Helper '/api/reconnect' @{ id = $id }); Refresh-State } catch { } } })
$removeBtn.Add_Click({
    $id = Selected-Id
    if (-not $id) { return }
    $folder = $list.SelectedItems[0].SubItems[1].Text
    $answer = [System.Windows.Forms.MessageBox]::Show($form, "Stop sharing $folder?`r`n`r`nThe folder on this computer stays as it is. Studio is told, and keeps its copy as an ordinary folder.", 'Eidoloom Studio helper', [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Question)
    if ($answer -eq [System.Windows.Forms.DialogResult]::Yes) {
        try {
            $done = Invoke-Helper '/api/remove' @{ id = $id }
            if (-not $done.studio_told) { Show-Message 'Studio could not be reached, so it still lists this folder. Disconnect it there too (Open folder, My computers).' }
            Refresh-State
        } catch { Show-Message ([string]$_.Exception.Message) }
    }
})

$autoBox.Add_CheckedChanged({
    if ($script:settingAuto) { return }
    try { [void](Invoke-Helper '/api/autostart' @{ on = $autoBox.Checked }) } catch { Show-Message ([string]$_.Exception.Message) }
})

$activityBtn.Add_Click({
    $win = New-Object System.Windows.Forms.Form
    $win.Text = 'Eidoloom Studio helper - activity'
    $win.Font = $font
    $win.ClientSize = New-Object System.Drawing.Size(760, 420)
    $win.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterParent
    $box = New-Object System.Windows.Forms.TextBox
    $box.Multiline = $true
    $box.ReadOnly = $true
    $box.ScrollBars = [System.Windows.Forms.ScrollBars]::Both
    $box.WordWrap = $false
    $box.Dock = [System.Windows.Forms.DockStyle]::Fill
    $box.Font = New-Object System.Drawing.Font('Consolas', 9)
    $box.Text = ($script:lastLog -join "`r`n")
    $win.Controls.Add($box)
    $win.Add_Shown({ $box.SelectionStart = $box.Text.Length; $box.ScrollToCaret() })
    [void]$win.ShowDialog($form)
})

$aboutBtn.Add_Click({
    $text = "Eidoloom Studio helper $Version`r`n`r`nShares folders on this computer with Eidoloom Studio: Studio keeps a copy in sync, and the agent's commands and your terminals run here.`r`n`r`nSettings: $Settings`r`nLog: $(Join-Path $Settings 'helper.log')`r`n`r`nStudio's My computers list shows which version each computer runs, and says when a newer one is available."
    [void][System.Windows.Forms.MessageBox]::Show($form, $text, 'About the Eidoloom Studio helper', [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Information)
})

$quitBtn.Add_Click({
    $answer = [System.Windows.Forms.MessageBox]::Show($form, 'Quit the helper? Every folder stops being shared until you start it again.', 'Eidoloom Studio helper', [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Question)
    if ($answer -eq [System.Windows.Forms.DialogResult]::Yes) {
        try { [void](Invoke-Helper '/api/quit' @{}) } catch { }
        $form.Close()
    }
})

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 2000
$timer.Add_Tick({ Refresh-State })
$form.Add_Shown({
    Refresh-State
    $timer.Start()
    $form.TopMost = $true
    $form.Activate()
    $form.TopMost = $false
})
$form.Add_FormClosed({ $timer.Stop() })
Update-Buttons
[void]$form.ShowDialog()
$mutex.ReleaseMutex()
