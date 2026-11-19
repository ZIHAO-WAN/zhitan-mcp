Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$token = [Console]::ReadLine()
$form = New-Object System.Windows.Forms.Form
$form.Text = '职探 MCP 本地配对'
$form.Size = New-Object System.Drawing.Size(540, 225)
$form.StartPosition = 'CenterScreen'
$label = New-Object System.Windows.Forms.Label
$label.Text = '复制本地密钥，仅粘贴到职探浏览器扩展，不要发送到聊天。'
$label.AutoSize = $true
$label.Location = New-Object System.Drawing.Point(15, 15)
$form.Controls.Add($label)
$field = New-Object System.Windows.Forms.TextBox
$field.Text = $token
$field.UseSystemPasswordChar = $true
$field.ReadOnly = $true
$field.Width = 480
$field.Location = New-Object System.Drawing.Point(15, 45)
$form.Controls.Add($field)
$button = New-Object System.Windows.Forms.Button
$button.Text = '复制密钥'
$button.AutoSize = $true
$button.Location = New-Object System.Drawing.Point(15, 80)
$button.Add_Click({ [System.Windows.Forms.Clipboard]::SetText($token) })
$form.Controls.Add($button)
$status = New-Object System.Windows.Forms.Label
$status.AutoSize = $true
$status.Location = New-Object System.Drawing.Point(15, 125)
$form.Controls.Add($status)
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 500
$timer.Add_Tick({
    try { $status.Text = [System.IO.File]::ReadAllText($env:BOSS_BRIDGE_STATUS_PATH, [System.Text.Encoding]::UTF8) } catch { }
})
$timer.Start()
[void]$form.ShowDialog()
$timer.Stop()
