using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Windows.Forms;
using System.Collections.Generic;
using System.Text;

namespace BrBrDbTelemetry
{
    public class InstallerForm : Form
    {
        // Styling Palette (Catppuccin Mocha-inspired premium dark theme)
        private static readonly Color ColorBg = Color.FromArgb(17, 17, 27);
        private static readonly Color ColorSidebar = Color.FromArgb(24, 24, 37);
        private static readonly Color ColorTitleBar = Color.FromArgb(30, 30, 46);
        private static readonly Color ColorAccent = Color.FromArgb(136, 57, 239); // Mauve/Purple
        private static readonly Color ColorAccentHover = Color.FromArgb(166, 129, 246);
        private static readonly Color ColorText = Color.FromArgb(205, 214, 244);
        private static readonly Color ColorTextMuted = Color.FromArgb(166, 173, 200);
        private static readonly Color ColorInputBg = Color.FromArgb(49, 50, 68);
        private static readonly Color ColorInputBorder = Color.FromArgb(69, 71, 90);
        private static readonly Color ColorSuccess = Color.FromArgb(166, 227, 161); // Green
        private static readonly Color ColorWarning = Color.FromArgb(249, 226, 175); // Peach/Yellow
        private static readonly Color ColorDanger = Color.FromArgb(243, 139, 168); // Red

        // App State
        private string selectedSimulator = "rf2"; // "rf2", "ace", "acr", or "ac"
        private string rf2Path = "";
        private string daemonInstallPath = "";

        private bool IsDaemonSimulator => selectedSimulator == "ace" || selectedSimulator == "acr" || selectedSimulator == "ac";

        private string CurrentDaemonInstallPath
        {
            get => daemonInstallPath;
            set => daemonInstallPath = value;
        }
        private PluginConfig config = new PluginConfig();
        private bool isInstalled = false;
        private string installedVersion = "";
        private string selectedTab = "install";

        // Simulator Selector Controls
        private Label lblSimSelectorTitle;
        private ComboBox cbSimulator;
        private Label lblHeaderInstall;
        private Label lblInstallPathHeader;

        // Daemon Status & Control Widget
        private Panel pnlDaemonStatus;
        private Label lblDaemonStatus;
        private CircleHelpButton btnDaemonHelp;
        private ToolTip toolTipDaemon;
        private Button btnDaemonToggle;
        private System.Windows.Forms.Timer timerDaemonCheck;

        // Dragging variables
        private bool isDragging = false;
        private Point dragCursorPoint;
        private Point dragFormPoint;

        // UI Components
        private Panel pnlTitleBar;
        private Panel pnlSidebar;
        private Panel pnlContent;
        private Label lblTitle;
        private Button btnClose;
        private Button btnMinimize;
        private PictureBox picAppIcon;

        // Sidebar Navigation Buttons
        private List<NavButton> navButtons = new List<NavButton>();
        private NavButton btnNavInstall;
        private NavButton btnNavUpdate;
        private NavButton btnNavGeneral;
        private NavButton btnNavSinks;
        private NavButton btnNavOverrides;
        private NavButton btnNavSessions;
        private NavButton btnNavLogs;
        private NavButton btnNavManage;

        // Content Panels
        private Panel tabInstall;
        private Panel tabUpdate;
        private Panel tabGeneral;
        private Panel tabSinks;
        private Panel tabOverrides;
        private Panel tabSessions;
        private Panel tabLogs;
        private Panel tabAbout;

        // Controls: Sessions Tab
        private ListView lvSessions;
        private Label lblSessionsStatus;
        private FlatButton btnReuploadSession;
        private FlatButton btnDeleteSession;
        private FlatButton btnOpenSessionsDir;
        private FlatButton btnRefreshSessions;

        // Controls: Logs Tab
        private TextBox txtLogsContent;
        private Label lblLogPathStatus;
        private FlatButton btnOpenLogExternal;
        private FlatButton btnRefreshLogs;

        // Controls: Install Tab
        private Panel pnlInstallControls;
        private Panel pnlInstallError;
        private Label lblInstallErrorMsg;
        private TextBox txtRf2Path;
        private TextBox txtApiKeyInstall;
        private TextBox txtTelemetryPathInstall;
        private FlatButton btnBrowsePath;
        private FlatButton btnBrowseTelemetry;
        private FlatButton btnInstall;

        // Controls: Update Tab
        private Label lblUpdateHeader;
        private Label lblUpdateQuestion;
        private FlatButton btnUpdate;

        // Controls: General Config Tab
        private TextBox txtCarRegexp;
        private TextBox txtKartNumberFilter;
        private TextBox txtLeague;
        private CheckBox chkAllParticipantsInRace;
        private FlatButton btnSaveGeneral;

        // Controls: Sinks Tab
        private ListBox lstSinks;
        private Panel pnlSinkEdit;
        private ComboBox cbSinkType;
        private CheckBox chkSinkEnabled;
        private Label lblSinkPath;
        private TextBox txtSinkPath;
        private FlatButton btnBrowseSinkPath;
        private Label lblSinkServer;
        private TextBox txtSinkServer;
        private Label lblSinkServerHelp;
        private Label lblSinkApiKey;
        private TextBox txtSinkApiKey;
        private Label lblSinkApiKeyHelp;
        private FlatButton btnSaveSinks;
        private FlatButton btnAddSink;
        private FlatButton btnDeleteSink;

        // Controls: Overrides Tab
        private ListBox lstOverrides;
        private Panel pnlOverrideEdit;
        private TextBox txtOverrideCarReg;
        private TextBox txtOverrideDriver;
        private FlatButton btnAddOverride;
        private FlatButton btnDeleteOverride;
        private FlatButton btnSaveOverrides;

        // Controls: About Tab
        private Label lblAboutStatus;
        private Panel pnlUpdateCard;
        private Label lblUpdateCardTitle;
        private Label lblUpdateStatus;
        private FlatButton btnCheckUpdates;
        private FlatButton btnViewRelease;
        private FlatButton btnOpenConfig;
        private FlatButton btnReinstall;
        private FlatButton btnUninstall;
        private InstallManager.GitHubReleaseInfo cachedReleaseInfo = null;
        private bool isCheckingForUpdates = false;

        public InstallerForm()
        {
            this.Size = new Size(820, 520);
            this.FormBorderStyle = FormBorderStyle.None;
            this.BackColor = ColorBg;
            this.ForeColor = ColorText;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.Text = "BrBrDb Telemetry";
            this.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            if (this.Icon == null)
            {
                throw new InvalidOperationException("Failed to load application icon: icon not found in executable.");
            }

            InitializeWindowChrome();
            InitializeSidebar();
            InitializeContentArea();

            // Auto-detect rFactor 2 path and initialize daemon paths
            rf2Path = InstallManager.DetectRFactor2Path();
            daemonInstallPath = InstallManager.GetDefaultDaemonInstallDirectory();
            txtRf2Path.Text = rf2Path;
        }

        protected override void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
            RefreshInstallationState();

            timerDaemonCheck = new System.Windows.Forms.Timer();
            timerDaemonCheck.Interval = 1500;
            timerDaemonCheck.Tick += (s, ev) =>
            {
                if (!this.IsDisposed)
                {
                    UpdateDaemonStatusUI();
                }
            };
            timerDaemonCheck.Start();
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            timerDaemonCheck?.Stop();
            timerDaemonCheck?.Dispose();
            base.OnFormClosing(e);
        }

        private void InitializeWindowChrome()
        {
            // Title Bar
            pnlTitleBar = new Panel
            {
                Size = new Size(this.Width, 40),
                Location = new Point(0, 0),
                BackColor = ColorTitleBar
            };
            pnlTitleBar.MouseDown += TitleBar_MouseDown;
            pnlTitleBar.MouseMove += TitleBar_MouseMove;
            pnlTitleBar.MouseUp += TitleBar_MouseUp;

            picAppIcon = new PictureBox
            {
                Size = new Size(22, 22),
                Location = new Point(12, 9),
                SizeMode = PictureBoxSizeMode.Zoom,
                Image = this.Icon.ToBitmap()
            };
            picAppIcon.MouseDown += TitleBar_MouseDown;
            picAppIcon.MouseMove += TitleBar_MouseMove;
            picAppIcon.MouseUp += TitleBar_MouseUp;
            pnlTitleBar.Controls.Add(picAppIcon);

            lblTitle = new Label
            {
                Text = "BrBrDb Telemetry",
                Font = new Font("Segoe UI", 10, FontStyle.Bold),
                ForeColor = ColorText,
                AutoSize = true,
                Location = new Point(38, 10)
            };
            lblTitle.MouseDown += TitleBar_MouseDown;
            lblTitle.MouseMove += TitleBar_MouseMove;
            lblTitle.MouseUp += TitleBar_MouseUp;

            btnClose = new Button
            {
                Text = "✕",
                Size = new Size(40, 40),
                Location = new Point(this.Width - 40, 0),
                FlatStyle = FlatStyle.Flat,
                ForeColor = ColorText,
                Font = new Font("Segoe UI", 10, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            btnClose.FlatAppearance.BorderSize = 0;
            btnClose.FlatAppearance.MouseOverBackColor = ColorDanger;
            btnClose.Click += (s, e) => this.Close();

            btnMinimize = new Button
            {
                Text = "—",
                Size = new Size(40, 40),
                Location = new Point(this.Width - 80, 0),
                FlatStyle = FlatStyle.Flat,
                ForeColor = ColorText,
                Font = new Font("Segoe UI", 10, FontStyle.Bold),
                Cursor = Cursors.Hand
            };
            btnMinimize.FlatAppearance.BorderSize = 0;
            btnMinimize.FlatAppearance.MouseOverBackColor = ColorInputBg;
            btnMinimize.Click += (s, e) => this.WindowState = FormWindowState.Minimized;

            pnlTitleBar.Controls.Add(lblTitle);
            pnlTitleBar.Controls.Add(btnMinimize);
            pnlTitleBar.Controls.Add(btnClose);
            this.Controls.Add(pnlTitleBar);
        }

        private void InitializeSidebar()
        {
            pnlSidebar = new Panel
            {
                Size = new Size(200, this.Height - 40),
                Location = new Point(0, 40),
                BackColor = ColorSidebar
            };

            lblSimSelectorTitle = new Label
            {
                Text = "SIMULATOR",
                Location = new Point(16, 12),
                AutoSize = true,
                Font = new Font("Segoe UI", 7.5f, FontStyle.Bold),
                ForeColor = ColorTextMuted
            };
            pnlSidebar.Controls.Add(lblSimSelectorTitle);

            cbSimulator = new ComboBox
            {
                Location = new Point(14, 30),
                Size = new Size(172, 28),
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = ColorInputBg,
                ForeColor = ColorText,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 9.5f, FontStyle.Bold)
            };
            cbSimulator.Items.AddRange(new object[] { "rFactor 2", "Assetto Corsa Evo", "Assetto Corsa Rally", "Assetto Corsa" });
            cbSimulator.SelectedIndex = 0;
            cbSimulator.SelectedIndexChanged += (s, e) => {
                switch (cbSimulator.SelectedIndex)
                {
                    case 0: selectedSimulator = "rf2"; break;
                    case 1: selectedSimulator = "ace"; break;
                    case 2: selectedSimulator = "acr"; break;
                    case 3: selectedSimulator = "ac"; break;
                    default: selectedSimulator = "rf2"; break;
                }
                OnSimulatorChanged();
            };
            pnlSidebar.Controls.Add(cbSimulator);

            // Daemon status & control container
            pnlDaemonStatus = new Panel
            {
                Location = new Point(14, 66),
                Size = new Size(172, 60),
                BackColor = Color.FromArgb(30, 30, 46),
                Visible = true
            };

            lblDaemonStatus = new Label
            {
                Location = new Point(6, 6),
                Size = new Size(138, 18),
                Font = new Font("Segoe UI", 8.5f, FontStyle.Bold),
                ForeColor = ColorTextMuted,
                Text = "○ Daemon: Stopped"
            };

            btnDaemonHelp = new CircleHelpButton
            {
                Location = new Point(148, 6),
                Size = new Size(18, 18)
            };

            toolTipDaemon = new ToolTip
            {
                AutoPopDelay = 9000,
                InitialDelay = 150,
                ReshowDelay = 100,
                ShowAlways = true
            };
            const string daemonHelpMsg = "Some games like Asseto Corsa series require a separate application running in parallel to collect the telemetry.";
            toolTipDaemon.SetToolTip(btnDaemonHelp, daemonHelpMsg);
            btnDaemonHelp.Click += (s, e) =>
            {
                toolTipDaemon.Show(daemonHelpMsg, btnDaemonHelp, 0, btnDaemonHelp.Height + 4, 6000);
            };

            btnDaemonToggle = new Button
            {
                Location = new Point(6, 26),
                Size = new Size(160, 26),
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 8.5f, FontStyle.Bold),
                ForeColor = Color.White,
                BackColor = ColorAccent,
                Cursor = Cursors.Hand,
                Text = "▶ Start Daemon"
            };
            btnDaemonToggle.FlatAppearance.BorderSize = 0;
            btnDaemonToggle.Click += BtnDaemonToggle_Click;

            pnlDaemonStatus.Controls.Add(lblDaemonStatus);
            pnlDaemonStatus.Controls.Add(btnDaemonHelp);
            pnlDaemonStatus.Controls.Add(btnDaemonToggle);
            pnlSidebar.Controls.Add(pnlDaemonStatus);

            btnNavInstall = AddNavButton("install", "Install");
            btnNavUpdate = AddNavButton("update", "Update");
            btnNavGeneral = AddNavButton("general", "General Settings");
            btnNavSinks = AddNavButton("sinks", "Sinks / Data Destinations");
            btnNavOverrides = AddNavButton("overrides", "Driver Overrides");
            btnNavSessions = AddNavButton("sessions", "Logged sessions");
            btnNavLogs = AddNavButton("logs", "Logs");
            btnNavManage = AddNavButton("manage", "Manage Installation");

            this.Controls.Add(pnlSidebar);
        }

        private NavButton AddNavButton(string id, string text)
        {
            var btn = new NavButton(id, text)
            {
                Size = new Size(200, 40)
            };
            btn.Click += (s, e) => SwitchToTab(btn.TabId);
            pnlSidebar.Controls.Add(btn);
            navButtons.Add(btn);
            return btn;
        }

        private void InitializeContentArea()
        {
            pnlContent = new Panel
            {
                Size = new Size(this.Width - 200, this.Height - 40),
                Location = new Point(200, 40),
                BackColor = ColorBg
            };

            InitializeInstallTab();
            InitializeUpdateTab();
            InitializeGeneralTab();
            InitializeSinksTab();
            InitializeOverridesTab();
            InitializeSessionsTab();
            InitializeLogsTab();
            InitializeAboutTab();

            this.Controls.Add(pnlContent);
        }

        private void InitializeInstallTab()
        {
            tabInstall = CreateTabPanel();

            lblHeaderInstall = new Label
            {
                Text = "Install BrBrTelemetry for rF2 v" + InstallManager.BundledVersion,
                Font = new Font("Segoe UI", 13, FontStyle.Bold),
                ForeColor = ColorAccent,
                Location = new Point(20, 20),
                AutoSize = true
            };
            tabInstall.Controls.Add(lblHeaderInstall);

            // 1. Controls Panel (for normal installation)
            pnlInstallControls = new Panel
            {
                Location = new Point(0, 60),
                Size = new Size(tabInstall.Width, tabInstall.Height - 60),
                BackColor = ColorBg
            };
            tabInstall.Controls.Add(pnlInstallControls);

            // Path to Simulator / Daemon
            lblInstallPathHeader = new Label
            {
                Text = "rFactor 2 Installation Path:",
                Font = new Font("Segoe UI", 9.5f, FontStyle.Bold),
                ForeColor = ColorText,
                Location = new Point(20, 5),
                AutoSize = true
            };
            pnlInstallControls.Controls.Add(lblInstallPathHeader);

            txtRf2Path = CreateTextBox(pnlInstallControls, 30, 460);
            txtRf2Path.ReadOnly = true;
            btnBrowsePath = new FlatButton("Browse...", 110, 25) { Location = new Point(490, 30), Visible = true };
            btnBrowsePath.Click += BrowseRf2Path_Click;
            pnlInstallControls.Controls.Add(btnBrowsePath);

            // API Key
            CreateFieldLabel(pnlInstallControls, "API Key:", 75);
            txtApiKeyInstall = CreateTextBox(pnlInstallControls, 100, 580);

            // Output Folder
            CreateFieldLabel(pnlInstallControls, "Save Telemetry Files To Folder:", 145);
            txtTelemetryPathInstall = CreateTextBox(pnlInstallControls, 170, 460);
            txtTelemetryPathInstall.Text = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "BrBrDbTelemetry");
            btnBrowseTelemetry = new FlatButton("Browse...", 110, 25) { Location = new Point(490, 170) };
            btnBrowseTelemetry.Click += BrowseTelemetryPath_Click;
            pnlInstallControls.Controls.Add(btnBrowseTelemetry);

            // Install Button (whole width of window minus margins: 580px)
            btnInstall = new FlatButton("Install Telemetry Plugin", 580, 40)
            {
                Location = new Point(20, 230),
                BackColor = ColorAccent,
                Font = new Font("Segoe UI", 11, FontStyle.Bold)
            };
            btnInstall.Click += Install_Click;
            pnlInstallControls.Controls.Add(btnInstall);

            // 2. Error Panel (when auto-detection fails)
            pnlInstallError = new Panel
            {
                Location = new Point(0, 60),
                Size = new Size(tabInstall.Width, tabInstall.Height - 60),
                BackColor = ColorBg,
                Visible = false
            };
            tabInstall.Controls.Add(pnlInstallError);

            lblInstallErrorMsg = new Label
            {
                Text = "Error: rFactor 2 installation directory could not be determined automatically.\nInstallation is not possible.",
                Location = new Point(20, 40),
                Size = new Size(580, 100),
                Font = new Font("Segoe UI", 12, FontStyle.Bold),
                ForeColor = ColorDanger
            };
            pnlInstallError.Controls.Add(lblInstallErrorMsg);
        }

        private void InitializeUpdateTab()
        {
            tabUpdate = CreateTabPanel();

            lblUpdateHeader = new Label
            {
                Text = "Update BrBrDb Telemetry",
                Font = new Font("Segoe UI", 13, FontStyle.Bold),
                ForeColor = ColorAccent,
                Location = new Point(20, 20),
                AutoSize = true
            };
            tabUpdate.Controls.Add(lblUpdateHeader);

            lblUpdateQuestion = new Label
            {
                Text = "Update BrBrDbTelemetry to version " + InstallManager.BundledVersion + "?",
                Location = new Point(20, 100),
                Size = new Size(580, 40),
                Font = new Font("Segoe UI", 12, FontStyle.Bold),
                ForeColor = ColorText
            };
            tabUpdate.Controls.Add(lblUpdateQuestion);

            btnUpdate = new FlatButton("Update", 580, 40)
            {
                Location = new Point(20, 160),
                BackColor = ColorAccent,
                Font = new Font("Segoe UI", 11, FontStyle.Bold)
            };
            btnUpdate.Click += Update_Click;
            tabUpdate.Controls.Add(btnUpdate);
        }

        private void InitializeGeneralTab()
        {
            tabGeneral = CreateTabPanel();

            CreateHeaderLabel(tabGeneral, "General Telemetry Settings", 20);

            CreateFieldLabel(tabGeneral, "Vehicle Name Regex Filter:", 65);
            txtCarRegexp = CreateTextBox(tabGeneral, 90, 580);
            Label lblCarHelp = new Label
            {
                Text = "Only sessions with the matching vehicle name will be recorded. " +
                       "Use 'KSP.*' to capture KartSim sessions, '.*' to match everything.",
                Location = new Point(20, 118),
                Size = new Size(580, 34),
                Font = new Font("Segoe UI", 8.5f, FontStyle.Italic),
                ForeColor = ColorTextMuted
            };
            tabGeneral.Controls.Add(lblCarHelp);

            CreateFieldLabel(tabGeneral, "Kart Number Filter:", 165);
            txtKartNumberFilter = CreateTextBox(tabGeneral, 190, 580);
            Label lblKartHelp = new Label
            {
                Text = "Only record sessions for this kart number. " +
                       "Leave blank to record all kart numbers.",
                Location = new Point(20, 218),
                Size = new Size(580, 34),
                Font = new Font("Segoe UI", 8.5f, FontStyle.Italic),
                ForeColor = ColorTextMuted
            };
            tabGeneral.Controls.Add(lblKartHelp);

            CreateFieldLabel(tabGeneral, "League Name:", 265);
            txtLeague = CreateTextBox(tabGeneral, 290, 580);
            Label lblLeagueHelp = new Label
            {
                Text = "Leave as 'kartsim' as BrBrDb won't accept other values.",
                Location = new Point(20, 318),
                Size = new Size(580, 34),
                Font = new Font("Segoe UI", 8.5f, FontStyle.Italic),
                ForeColor = ColorTextMuted
            };
            tabGeneral.Controls.Add(lblLeagueHelp);

            chkAllParticipantsInRace = new CheckBox
            {
                Text = "Collect telemetry for all participants in a race",
                Location = new Point(20, 360),
                Size = new Size(580, 25),
                Font = new Font("Segoe UI", 9.5f),
                ForeColor = ColorText
            };
            tabGeneral.Controls.Add(chkAllParticipantsInRace);

            Label lblAllParticipantsHelp = new Label
            {
                Text = "When enabled, telemetry will be recorded for all participants in the session instead of only the main player.",
                Location = new Point(20, 388),
                Size = new Size(580, 20),
                Font = new Font("Segoe UI", 8.5f, FontStyle.Italic),
                ForeColor = ColorTextMuted
            };
            tabGeneral.Controls.Add(lblAllParticipantsHelp);

            btnSaveGeneral = new FlatButton("Save Configuration", 200, 35)
            {
                Location = new Point(20, 420),
                BackColor = ColorAccent
            };
            btnSaveGeneral.Click += SaveGeneral_Click;
            tabGeneral.Controls.Add(btnSaveGeneral);
        }

        private void InitializeSinksTab()
        {
            tabSinks = CreateTabPanel();

            CreateHeaderLabel(tabSinks, "Sinks / Data Destinations", 20);

            lstSinks = new ListBox
            {
                Location = new Point(20, 70),
                Size = new Size(180, 300),
                BackColor = ColorSidebar,
                ForeColor = ColorText,
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Segoe UI", 10)
            };
            lstSinks.SelectedIndexChanged += LstSinks_SelectedIndexChanged;
            tabSinks.Controls.Add(lstSinks);

            btnAddSink = new FlatButton("＋ Add Sink", 85, 30) { Location = new Point(20, 380) };
            btnAddSink.Click += AddSink_Click;
            tabSinks.Controls.Add(btnAddSink);

            btnDeleteSink = new FlatButton("✕ Delete", 85, 30) { Location = new Point(115, 380), BackColor = ColorInputBg };
            btnDeleteSink.Click += DeleteSink_Click;
            tabSinks.Controls.Add(btnDeleteSink);

            // Editing fields container
            pnlSinkEdit = new Panel
            {
                Location = new Point(220, 70),
                Size = new Size(380, 300),
                Visible = false
            };

            Label lblType = new Label { Text = "Sink Type:", Location = new Point(0, 5), AutoSize = true, Font = new Font("Segoe UI", 9.5f, FontStyle.Bold) };
            cbSinkType = new ComboBox
            {
                Location = new Point(100, 2),
                Size = new Size(150, 25),
                DropDownStyle = ComboBoxStyle.DropDownList,
                BackColor = ColorInputBg,
                ForeColor = ColorText,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 9.5f)
            };
            cbSinkType.Items.AddRange(new string[] { "file", "http" });
            cbSinkType.SelectedIndexChanged += CbSinkType_SelectedIndexChanged;
            pnlSinkEdit.Controls.Add(lblType);
            pnlSinkEdit.Controls.Add(cbSinkType);

            chkSinkEnabled = new CheckBox
            {
                Text = "Enabled",
                Location = new Point(270, 2),
                Size = new Size(100, 25),
                Font = new Font("Segoe UI", 9.5f),
                ForeColor = ColorText
            };
            chkSinkEnabled.CheckedChanged += ChkSinkEnabled_CheckedChanged;
            pnlSinkEdit.Controls.Add(chkSinkEnabled);

            // File sink path
            lblSinkPath = new Label { Text = "Output Path:", Location = new Point(0, 55), AutoSize = true };
            txtSinkPath = new TextBox { Location = new Point(0, 80), Size = new Size(250, 25), BackColor = ColorInputBg, ForeColor = ColorText, BorderStyle = BorderStyle.FixedSingle, Font = new Font("Segoe UI", 9.5f) };
            txtSinkPath.TextChanged += TxtSinkPath_TextChanged;
            btnBrowseSinkPath = new FlatButton("Browse...", 100, 26) { Location = new Point(260, 79) };
            btnBrowseSinkPath.Click += BrowseSinkPath_Click;
            pnlSinkEdit.Controls.Add(lblSinkPath);
            pnlSinkEdit.Controls.Add(txtSinkPath);
            pnlSinkEdit.Controls.Add(btnBrowseSinkPath);

            // HTTP settings
            lblSinkServer = new Label { Text = "Server Address:", Location = new Point(0, 55), AutoSize = true };
            txtSinkServer = new TextBox { Location = new Point(0, 80), Size = new Size(350, 25), BackColor = ColorInputBg, ForeColor = ColorText, BorderStyle = BorderStyle.FixedSingle, Font = new Font("Segoe UI", 9.5f) };
            txtSinkServer.TextChanged += TxtSinkServer_TextChanged;
            lblSinkServerHelp = new Label
            {
                Text = "Should be https://brbrdb.brbrkitten.com unless you stream to a custom server.",
                Location = new Point(0, 108),
                Size = new Size(360, 30),
                Font = new Font("Segoe UI", 7.5f, FontStyle.Italic),
                ForeColor = ColorTextMuted
            };
            pnlSinkEdit.Controls.Add(lblSinkServer);
            pnlSinkEdit.Controls.Add(txtSinkServer);
            pnlSinkEdit.Controls.Add(lblSinkServerHelp);

            lblSinkApiKey = new Label { Text = "API Key:", Location = new Point(0, 145), AutoSize = true };
            txtSinkApiKey = new TextBox { Location = new Point(0, 170), Size = new Size(350, 25), BackColor = ColorInputBg, ForeColor = ColorText, BorderStyle = BorderStyle.FixedSingle, Font = new Font("Segoe UI", 9.5f) };
            txtSinkApiKey.TextChanged += TxtSinkApiKey_TextChanged;
            lblSinkApiKeyHelp = new Label
            {
                Text = "Authentication key issued by the server administrator.",
                Location = new Point(0, 198),
                Size = new Size(360, 30),
                Font = new Font("Segoe UI", 7.5f, FontStyle.Italic),
                ForeColor = ColorTextMuted
            };
            pnlSinkEdit.Controls.Add(lblSinkApiKey);
            pnlSinkEdit.Controls.Add(txtSinkApiKey);
            pnlSinkEdit.Controls.Add(lblSinkApiKeyHelp);

            tabSinks.Controls.Add(pnlSinkEdit);

            btnSaveSinks = new FlatButton("Save Sinks Configuration", 220, 30)
            {
                Location = new Point(220, 380),
                BackColor = ColorAccent
            };
            btnSaveSinks.Click += SaveSinks_Click;
            tabSinks.Controls.Add(btnSaveSinks);
        }

        private void InitializeOverridesTab()
        {
            tabOverrides = CreateTabPanel();

            CreateHeaderLabel(tabOverrides, "Configure Driver Name Overrides", 20);

            lstOverrides = new ListBox
            {
                Location = new Point(20, 70),
                Size = new Size(180, 300),
                BackColor = ColorSidebar,
                ForeColor = ColorText,
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Segoe UI", 10)
            };
            lstOverrides.SelectedIndexChanged += LstOverrides_SelectedIndexChanged;
            tabOverrides.Controls.Add(lstOverrides);

            btnAddOverride = new FlatButton("＋ Add Override", 85, 30) { Location = new Point(20, 380) };
            btnAddOverride.Click += AddOverride_Click;
            tabOverrides.Controls.Add(btnAddOverride);

            btnDeleteOverride = new FlatButton("✕ Delete", 85, 30) { Location = new Point(115, 380), BackColor = ColorInputBg };
            btnDeleteOverride.Click += DeleteOverride_Click;
            tabOverrides.Controls.Add(btnDeleteOverride);

            // Editing fields container
            pnlOverrideEdit = new Panel
            {
                Location = new Point(220, 70),
                Size = new Size(380, 300),
                Visible = false
            };

            CreateFieldLabel(pnlOverrideEdit, "Car Name Regex Filter:", 5, 0);
            txtOverrideCarReg = new TextBox { Location = new Point(0, 30), Size = new Size(350, 25), BackColor = ColorInputBg, ForeColor = ColorText, BorderStyle = BorderStyle.FixedSingle, Font = new Font("Segoe UI", 9.5f) };
            txtOverrideCarReg.TextChanged += TxtOverrideCarReg_TextChanged;
            txtOverrideCarReg.Leave += TxtOverrideFields_Leave;
            pnlOverrideEdit.Controls.Add(txtOverrideCarReg);
            var lblOverrideCarHelp = new Label
            {
                Text = "Allows distinguishing real training sessions from test and fun runs by selecting specific karts. For example I use '#8' for validating that telemetry is working as intended.",
                Location = new Point(0, 58),
                Size = new Size(350, 30),
                Font = new Font("Segoe UI", 7.5f, FontStyle.Italic),
                ForeColor = ColorTextMuted
            };
            pnlOverrideEdit.Controls.Add(lblOverrideCarHelp);

            CreateFieldLabel(pnlOverrideEdit, "Override Driver Name To:", 100, 0);
            txtOverrideDriver = new TextBox { Location = new Point(0, 125), Size = new Size(350, 25), BackColor = ColorInputBg, ForeColor = ColorText, BorderStyle = BorderStyle.FixedSingle, Font = new Font("Segoe UI", 9.5f) };
            txtOverrideDriver.TextChanged += TxtOverrideDriver_TextChanged;
            txtOverrideDriver.Leave += TxtOverrideFields_Leave;
            pnlOverrideEdit.Controls.Add(txtOverrideDriver);

            tabOverrides.Controls.Add(pnlOverrideEdit);

            btnSaveOverrides = new FlatButton("Save Overrides Configuration", 220, 30)
            {
                Location = new Point(220, 380),
                BackColor = ColorAccent
            };
            btnSaveOverrides.Click += SaveOverrides_Click;
            tabOverrides.Controls.Add(btnSaveOverrides);
        }

        private void InitializeAboutTab()
        {
            tabAbout = CreateTabPanel();

            CreateHeaderLabel(tabAbout, "Manage Installation", 20);

            lblAboutStatus = new Label
            {
                Location = new Point(20, 60),
                Size = new Size(580, 115),
                Font = new Font("Segoe UI", 9.5f),
                ForeColor = ColorText,
                Text = "Loading details..."
            };
            tabAbout.Controls.Add(lblAboutStatus);

            pnlUpdateCard = new Panel
            {
                Location = new Point(20, 185),
                Size = new Size(580, 82),
                BackColor = ColorTitleBar
            };

            lblUpdateCardTitle = new Label
            {
                Text = "GitHub Release Check",
                Font = new Font("Segoe UI", 9.5f, FontStyle.Bold),
                ForeColor = ColorText,
                Location = new Point(14, 10),
                AutoSize = true
            };
            pnlUpdateCard.Controls.Add(lblUpdateCardTitle);

            lblUpdateStatus = new Label
            {
                Text = "Click 'Check for Updates' to query GitHub.",
                Font = new Font("Segoe UI", 8.5f),
                ForeColor = ColorTextMuted,
                Location = new Point(14, 32),
                Size = new Size(385, 42)
            };
            pnlUpdateCard.Controls.Add(lblUpdateStatus);

            btnCheckUpdates = new FlatButton("Check for Updates", 160, 30)
            {
                Location = new Point(406, 10),
                BackColor = ColorInputBg,
                Font = new Font("Segoe UI", 8.5f)
            };
            btnCheckUpdates.Click += (s, e) => TriggerGitHubUpdateCheck(forceRefresh: true);
            pnlUpdateCard.Controls.Add(btnCheckUpdates);

            btnViewRelease = new FlatButton("View on GitHub", 160, 30)
            {
                Location = new Point(406, 44),
                BackColor = ColorAccent,
                Font = new Font("Segoe UI", 8.5f),
                Visible = false
            };
            btnViewRelease.Click += ViewRelease_Click;
            pnlUpdateCard.Controls.Add(btnViewRelease);

            tabAbout.Controls.Add(pnlUpdateCard);

            btnOpenConfig = new FlatButton("Open Configuration File", 580, 36)
            {
                Location = new Point(20, 280),
                BackColor = ColorInputBg
            };
            btnOpenConfig.Click += OpenConfig_Click;
            tabAbout.Controls.Add(btnOpenConfig);

            btnReinstall = new FlatButton("Reinstall BrBrDb Telemetry", 580, 36)
            {
                Location = new Point(20, 326),
                BackColor = ColorAccent
            };
            btnReinstall.Click += Reinstall_Click;
            tabAbout.Controls.Add(btnReinstall);

            btnUninstall = new FlatButton("Uninstall BrBrDb Telemetry", 580, 36)
            {
                Location = new Point(20, 372),
                BackColor = ColorDanger
            };
            btnUninstall.Click += Uninstall_Click;
            tabAbout.Controls.Add(btnUninstall);
        }

        private void InitializeSessionsTab()
        {
            tabSessions = CreateTabPanel();

            CreateHeaderLabel(tabSessions, "Logged Telemetry Sessions", 20);

            lblSessionsStatus = new Label
            {
                Location = new Point(20, 55),
                Size = new Size(580, 20),
                Font = new Font("Segoe UI", 8.5f, FontStyle.Italic),
                ForeColor = ColorTextMuted,
                Text = "Directory: "
            };
            tabSessions.Controls.Add(lblSessionsStatus);

            lvSessions = new ListView
            {
                Location = new Point(20, 80),
                Size = new Size(580, 290),
                View = View.Details,
                FullRowSelect = true,
                GridLines = true,
                MultiSelect = true,
                HeaderStyle = ColumnHeaderStyle.Nonclickable,
                BackColor = ColorSidebar,
                ForeColor = ColorText,
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Segoe UI", 9.5f)
            };
            lvSessions.Columns.Add("Date", 100);
            lvSessions.Columns.Add("Time", 80);
            lvSessions.Columns.Add("Track", 301);
            lvSessions.Columns.Add("Size", 95);
            lvSessions.ClientSizeChanged += (s, e) => AdjustSessionColumnWidths();
            lvSessions.ColumnWidthChanged += (s, e) => { if (e.ColumnIndex != 2) AdjustSessionColumnWidths(); };
            lvSessions.KeyDown += LvSessions_KeyDown;
            tabSessions.Controls.Add(lvSessions);

            btnReuploadSession = new FlatButton("Re-upload", 140, 36)
            {
                Location = new Point(20, 380),
                BackColor = ColorAccent
            };
            btnReuploadSession.Click += ReuploadSession_Click;
            tabSessions.Controls.Add(btnReuploadSession);

            btnDeleteSession = new FlatButton("Delete", 120, 36)
            {
                Location = new Point(170, 380),
                BackColor = ColorInputBg
            };
            btnDeleteSession.Click += DeleteSession_Click;
            tabSessions.Controls.Add(btnDeleteSession);

            btnOpenSessionsDir = new FlatButton("Open in File Manager", 160, 36)
            {
                Location = new Point(300, 380),
                BackColor = ColorInputBg
            };
            btnOpenSessionsDir.Click += OpenSessionsDir_Click;
            tabSessions.Controls.Add(btnOpenSessionsDir);

            btnRefreshSessions = new FlatButton("Refresh List", 130, 36)
            {
                Location = new Point(470, 380),
                BackColor = ColorInputBg
            };
            btnRefreshSessions.Click += (s, e) => RefreshSessionsList();
            tabSessions.Controls.Add(btnRefreshSessions);
        }

        private void InitializeLogsTab()
        {
            tabLogs = CreateTabPanel();

            CreateHeaderLabel(tabLogs, "Telemetry Plugin Log", 20);

            lblLogPathStatus = new Label
            {
                Location = new Point(20, 55),
                Size = new Size(580, 20),
                Font = new Font("Segoe UI", 8.5f, FontStyle.Italic),
                ForeColor = ColorTextMuted,
                Text = "Log File: "
            };
            tabLogs.Controls.Add(lblLogPathStatus);

            txtLogsContent = new TextBox
            {
                Location = new Point(20, 80),
                Size = new Size(580, 290),
                Multiline = true,
                ReadOnly = true,
                ScrollBars = ScrollBars.Both,
                WordWrap = false,
                BackColor = ColorInputBg,
                ForeColor = ColorText,
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Consolas", 9f)
            };
            tabLogs.Controls.Add(txtLogsContent);

            btnOpenLogExternal = new FlatButton("Open Log File in External Editor", 250, 36)
            {
                Location = new Point(20, 380),
                BackColor = ColorAccent
            };
            btnOpenLogExternal.Click += OpenLogExternal_Click;
            tabLogs.Controls.Add(btnOpenLogExternal);

            btnRefreshLogs = new FlatButton("Refresh Log", 140, 36)
            {
                Location = new Point(280, 380),
                BackColor = ColorInputBg
            };
            btnRefreshLogs.Click += (s, e) => RefreshLogsContent();
            tabLogs.Controls.Add(btnRefreshLogs);
        }

        private string GetActiveConfigPath()
        {
            if (IsDaemonSimulator)
                return Path.Combine(CurrentDaemonInstallPath, "BrBrDbTelemetry.json");
            return Path.Combine(rf2Path, @"Bin64\Plugins\BrBrDbTelemetry.json");
        }

        private string GetActiveLogPath()
        {
            if (IsDaemonSimulator)
                return InstallManager.GetDaemonLogFilePath(CurrentDaemonInstallPath);
            return InstallManager.GetLogFilePath(rf2Path);
        }

        private string GetActiveSessionsPath()
        {
            if (IsDaemonSimulator)
                return InstallManager.GetAceSessionsDirectoryPath(config.sinks.Find(s => s.type == "file")?.path, CurrentDaemonInstallPath);
            return InstallManager.GetSessionsDirectoryPath(rf2Path, config);
        }

        private void OnSimulatorChanged()
        {
            if (IsDaemonSimulator)
            {
                string simName = InstallManager.GetSimulatorDisplayName(selectedSimulator);
                lblInstallPathHeader.Text = "Assetto Corsa Telemetry Daemon Folder:";
                txtRf2Path.Text = CurrentDaemonInstallPath;
                lblHeaderInstall.Text = "Install BrBrDb Telemetry for " + simName + " v" + InstallManager.BundledAceVersion;
            }
            else
            {
                lblInstallPathHeader.Text = "rFactor 2 Installation Path:";
                txtRf2Path.Text = rf2Path;
                lblHeaderInstall.Text = "Install BrBrDb Telemetry for rF2 v" + InstallManager.BundledVersion;
            }
            RefreshInstallationState();
            cachedReleaseInfo = null;
            if (tabAbout != null && tabAbout.Visible)
            {
                TriggerGitHubUpdateCheck(forceRefresh: true);
            }
        }

        private void RefreshSessionsList()
        {
            lvSessions.Items.Clear();

            string dirPath = GetActiveSessionsPath();
            lblSessionsStatus.Text = "Directory: " + dirPath;

            FileInfo[] files = InstallManager.GetSessionFiles(dirPath);
            foreach (var file in files)
            {
                var meta = InstallManager.GetSessionMetadata(file);
                var item = new ListViewItem(meta.Date);
                item.SubItems.Add(meta.Time);
                item.SubItems.Add(meta.Track);
                item.SubItems.Add(string.Format("{0:F2} MB", meta.SizeMb));
                item.Tag = meta;
                lvSessions.Items.Add(item);
            }

            if (files.Length == 0)
            {
                lblSessionsStatus.Text = "Directory: " + dirPath + " (No session .csv files found)";
            }

            AdjustSessionColumnWidths();
        }

        private bool adjustingColumns = false;
        private void AdjustSessionColumnWidths()
        {
            if (adjustingColumns || lvSessions == null || lvSessions.Columns.Count < 4) return;
            try
            {
                adjustingColumns = true;
                int fixedWidth = lvSessions.Columns[0].Width + lvSessions.Columns[1].Width + lvSessions.Columns[3].Width;
                int clientW = lvSessions.ClientSize.Width;
                int remaining = clientW - fixedWidth;
                if (remaining > 50)
                {
                    lvSessions.Columns[2].Width = remaining;
                }
            }
            finally
            {
                adjustingColumns = false;
            }
        }

        private void LvSessions_KeyDown(object sender, KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Delete)
            {
                DeleteSelectedSessions();
                e.Handled = true;
            }
            else if (e.Control && e.KeyCode == Keys.A)
            {
                foreach (ListViewItem item in lvSessions.Items)
                {
                    item.Selected = true;
                }
                e.Handled = true;
            }
        }

        private void RefreshLogsContent()
        {
            string logPath = GetActiveLogPath();
            lblLogPathStatus.Text = "Log File: " + logPath;

            if (!string.IsNullOrEmpty(logPath) && File.Exists(logPath))
            {
                try
                {
                    using (var stream = new FileStream(logPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
                    using (var reader = new StreamReader(stream))
                    {
                        txtLogsContent.Text = reader.ReadToEnd();
                    }
                    txtLogsContent.SelectionStart = txtLogsContent.Text.Length;
                    txtLogsContent.ScrollToCaret();
                }
                catch (Exception ex)
                {
                    txtLogsContent.Text = "Error reading log file: " + ex.Message;
                }
            }
            else
            {
                txtLogsContent.Text = "Log file does not exist yet at:\r\n" + logPath;
            }
        }

        private void OpenSessionsDir_Click(object sender, EventArgs e)
        {
            string dirPath = GetActiveSessionsPath();
            if (lvSessions.SelectedItems.Count > 0)
            {
                var meta = lvSessions.SelectedItems[0].Tag as InstallManager.SessionMetadata;
                if (meta != null && meta.FileInfo != null && meta.FileInfo.Exists)
                {
                    try
                    {
                        System.Diagnostics.Process.Start("explorer.exe", string.Format("/select,\"{0}\"", meta.FileInfo.FullName));
                        return;
                    }
                    catch {}
                }
            }

            if (Directory.Exists(dirPath))
            {
                try
                {
                    System.Diagnostics.Process.Start("explorer.exe", string.Format("\"{0}\"", dirPath));
                }
                catch (Exception ex)
                {
                    MessageBox.Show("Failed to open file manager: " + ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
            else
            {
                MessageBox.Show("Session directory does not exist yet.", "Directory Not Found", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
        }

        private void ReuploadSession_Click(object sender, EventArgs e)
        {
            if (lvSessions.SelectedItems.Count == 0)
            {
                MessageBox.Show("Please select one or more sessions from the table to re-upload.", "No Session Selected", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }

            var selectedSessions = new List<InstallManager.SessionMetadata>();
            foreach (ListViewItem item in lvSessions.SelectedItems)
            {
                var meta = item.Tag as InstallManager.SessionMetadata;
                if (meta != null && meta.FileInfo != null && meta.FileInfo.Exists)
                {
                    selectedSessions.Add(meta);
                }
            }

            if (selectedSessions.Count == 0)
            {
                MessageBox.Show("Selected session file(s) could not be found on disk.", "Files Missing", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            string serverAddress = "https://brbrdb.brbrkitten.com";
            string apiKey = "";

            SinkConfig httpSink = config.sinks != null ? config.sinks.Find(s => s.type == "http") : null;
            if (httpSink != null)
            {
                if (!string.IsNullOrEmpty(httpSink.server_address))
                    serverAddress = httpSink.server_address;
                apiKey = httpSink.api_key;
            }
            else if (!string.IsNullOrEmpty(txtApiKeyInstall.Text.Trim()))
            {
                apiKey = txtApiKeyInstall.Text.Trim();
            }

            DialogResult confirmResult;
            if (selectedSessions.Count == 1)
            {
                var meta = selectedSessions[0];
                confirmResult = MessageBox.Show(
                    string.Format("Re-upload session for track '{0}' ({1} {2}, {3:F2} MB) to {4}?",
                        meta.Track, meta.Date, meta.Time, meta.SizeMb, serverAddress),
                    "Confirm Re-upload",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Question);
            }
            else
            {
                double totalSizeMb = 0;
                foreach (var s in selectedSessions)
                    totalSizeMb += s.SizeMb;

                confirmResult = MessageBox.Show(
                    string.Format("Re-upload {0} selected sessions (total {1:F2} MB) to {2}?",
                        selectedSessions.Count, totalSizeMb, serverAddress),
                    "Confirm Re-upload",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Question);
            }

            if (confirmResult != DialogResult.Yes)
                return;

            this.Cursor = Cursors.WaitCursor;
            btnReuploadSession.Enabled = false;
            if (btnDeleteSession != null) btnDeleteSession.Enabled = false;
            btnRefreshSessions.Enabled = false;

            int successCount = 0;
            int failCount = 0;
            var errors = new List<string>();

            for (int i = 0; i < selectedSessions.Count; i++)
            {
                var session = selectedSessions[i];
                lblSessionsStatus.Text = string.Format("Uploading ({0}/{1}): '{2}' to {3}...",
                    i + 1, selectedSessions.Count, session.Track, serverAddress);
                this.Refresh();

                string errorMsg;
                bool success = InstallManager.UploadSessionFile(session.FileInfo.FullName, serverAddress, apiKey, out errorMsg);
                if (success)
                {
                    successCount++;
                }
                else
                {
                    failCount++;
                    errors.Add(string.Format("{0} ({1}): {2}", session.Track, session.FileInfo.Name, errorMsg));
                }
            }

            this.Cursor = Cursors.Default;
            btnReuploadSession.Enabled = true;
            if (btnDeleteSession != null) btnDeleteSession.Enabled = true;
            btnRefreshSessions.Enabled = true;
            lblSessionsStatus.Text = "Directory: " + InstallManager.GetSessionsDirectoryPath(rf2Path, config);

            if (selectedSessions.Count == 1)
            {
                if (successCount == 1)
                {
                    MessageBox.Show(
                        string.Format("Successfully re-uploaded telemetry session for '{0}' to {1}!", selectedSessions[0].Track, serverAddress),
                        "Upload Successful",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Information);
                }
                else
                {
                    MessageBox.Show(
                        string.Format("Failed to re-upload session to {0}.\n\nError details: {1}", serverAddress, errors.Count > 0 ? errors[0] : "Unknown error"),
                        "Upload Failed",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error);
                }
            }
            else
            {
                if (failCount == 0)
                {
                    MessageBox.Show(
                        string.Format("Successfully re-uploaded all {0} telemetry sessions to {1}!", successCount, serverAddress),
                        "Upload Successful",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Information);
                }
                else if (successCount > 0)
                {
                    MessageBox.Show(
                        string.Format("Uploaded {0} of {1} sessions to {2}.\n\n{3} sessions failed:\n{4}",
                            successCount, selectedSessions.Count, serverAddress, failCount, string.Join("\n", errors.ToArray())),
                        "Upload Partially Completed",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Warning);
                }
                else
                {
                    MessageBox.Show(
                        string.Format("Failed to re-upload {0} sessions to {1}.\n\nError details:\n{2}",
                            failCount, serverAddress, string.Join("\n", errors.ToArray())),
                        "Upload Failed",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error);
                }
            }
        }

        private void DeleteSession_Click(object sender, EventArgs e)
        {
            DeleteSelectedSessions();
        }

        private void DeleteSelectedSessions()
        {
            if (lvSessions.SelectedItems.Count == 0)
            {
                MessageBox.Show("Please select one or more sessions from the table to delete.", "No Session Selected", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }

            var selectedSessions = new List<InstallManager.SessionMetadata>();
            foreach (ListViewItem item in lvSessions.SelectedItems)
            {
                var meta = item.Tag as InstallManager.SessionMetadata;
                if (meta != null && meta.FileInfo != null)
                {
                    selectedSessions.Add(meta);
                }
            }

            if (selectedSessions.Count == 0)
                return;

            DialogResult confirmResult;
            if (selectedSessions.Count == 1)
            {
                var meta = selectedSessions[0];
                confirmResult = MessageBox.Show(
                    string.Format("Are you sure you want to permanently delete the session for track '{0}' ({1} {2}, {3:F2} MB)?\n\nFile: {4}",
                        meta.Track, meta.Date, meta.Time, meta.SizeMb, meta.FileInfo.Name),
                    "Confirm Deletion",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Warning);
            }
            else
            {
                double totalSizeMb = 0;
                foreach (var s in selectedSessions)
                    totalSizeMb += s.SizeMb;

                confirmResult = MessageBox.Show(
                    string.Format("Are you sure you want to permanently delete {0} selected sessions (total {1:F2} MB)?",
                        selectedSessions.Count, totalSizeMb),
                    "Confirm Deletion",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Warning);
            }

            if (confirmResult != DialogResult.Yes)
                return;

            int deletedCount = 0;
            var errors = new List<string>();

            foreach (var session in selectedSessions)
            {
                string errorMsg;
                if (InstallManager.DeleteSessionFile(session.FileInfo.FullName, out errorMsg))
                {
                    deletedCount++;
                }
                else
                {
                    errors.Add(string.Format("{0}: {1}", session.FileInfo.Name, errorMsg));
                }
            }

            RefreshSessionsList();

            if (errors.Count > 0)
            {
                MessageBox.Show(
                    string.Format("Failed to delete {0} of {1} session file(s):\n\n{2}",
                        errors.Count, selectedSessions.Count, string.Join("\n", errors.ToArray())),
                    "Deletion Error",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
            }
        }

        private void OpenLogExternal_Click(object sender, EventArgs e)
        {
            string logPath = GetActiveLogPath();
            if (!string.IsNullOrEmpty(logPath) && File.Exists(logPath))
            {
                try
                {
                    var psi = new System.Diagnostics.ProcessStartInfo(logPath) { UseShellExecute = true };
                    System.Diagnostics.Process.Start(psi);
                }
                catch (Exception ex)
                {
                    MessageBox.Show("Failed to open log file in external editor: " + ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
            else
            {
                MessageBox.Show("Log file does not exist yet at:\r\n" + logPath, "File Not Found", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
        }

        private Panel CreateTabPanel()
        {
            var pnl = new Panel
            {
                Size = new Size(pnlContent.Width, pnlContent.Height),
                Location = new Point(0, 0),
                BackColor = ColorBg,
                Visible = false
            };
            pnlContent.Controls.Add(pnl);
            return pnl;
        }

        private void CreateHeaderLabel(Panel parent, string text, int y)
        {
            var lbl = new Label
            {
                Text = text,
                Font = new Font("Segoe UI", 13, FontStyle.Bold),
                ForeColor = ColorAccent,
                Location = new Point(20, y),
                AutoSize = true
            };
            parent.Controls.Add(lbl);
        }

        private void CreateFieldLabel(Panel parent, string text, int y, int x = 20)
        {
            var lbl = new Label
            {
                Text = text,
                Font = new Font("Segoe UI", 9.5f, FontStyle.Bold),
                ForeColor = ColorText,
                Location = new Point(x, y),
                AutoSize = true
            };
            parent.Controls.Add(lbl);
        }

        private TextBox CreateTextBox(Panel parent, int y, int width, int x = 20)
        {
            var txt = new TextBox
            {
                Location = new Point(x, y),
                Width = width,
                Height = 25,
                BackColor = ColorInputBg,
                ForeColor = ColorText,
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Segoe UI", 10)
            };
            parent.Controls.Add(txt);
            return txt;
        }

        private void RefreshInstallationState()
        {
            if (IsDaemonSimulator)
            {
                pnlInstallControls.Visible = true;
                pnlInstallError.Visible = false;
                btnInstall.Enabled = true;
                btnOpenConfig.Enabled = true;
                btnReinstall.Enabled = true;

                bool isRunning = false;
                bool isAutostart = false;
                isInstalled = InstallManager.GetInstalledDaemonDetails(selectedSimulator, CurrentDaemonInstallPath, out installedVersion, out isRunning, out isAutostart);

                string activeBundled = InstallManager.BundledAceVersion;
                if (isInstalled)
                {
                    btnUninstall.Enabled = true;
                    bool isNewer = InstallManager.IsInstalledVersionNewer(installedVersion, activeBundled);
                    btnNavUpdate.LabelText = isNewer ? "Downgrade" : "Update";

                    if (installedVersion != activeBundled)
                    {
                        btnInstall.Text = isNewer ? "Downgrade Telemetry Daemon" : "Update Telemetry Daemon";
                        lblUpdateHeader.Text = isNewer ? "Downgrade BrBrDb Telemetry" : "Update BrBrDb Telemetry";
                        btnUpdate.Text = isNewer ? "Downgrade" : "Update";
                        lblUpdateQuestion.Text = string.Format(
                            isNewer 
                                ? "A newer version of the daemon is installed (v{0}). Downgrade BrBrDbTelemetry to version {1}?" 
                                : "An older version of the daemon is installed (v{0}). Update BrBrDbTelemetry to version {1}?", 
                            installedVersion, 
                            activeBundled
                        );
                    }
                    else
                    {
                        btnInstall.Text = "Reinstall Telemetry Daemon";
                    }

                    LoadConfigFromFile();
                }
                else
                {
                    btnInstall.Text = "Install Telemetry Daemon";
                    btnUninstall.Enabled = false;
                    btnNavUpdate.LabelText = "Update";
                }

                string simName = InstallManager.GetSimulatorDisplayName(selectedSimulator);
                lblAboutStatus.Text = string.Format("Assetto Corsa Telemetry Daemon Status:\n" +
                                      "--------------------------------------\n" +
                                      "Configuring Simulator: {0}\n" +
                                      "Shared Daemon Folder: {1}\n" +
                                      "Daemon Installed: {2}\n" +
                                      "Daemon Running: {3}\n" +
                                      "Windows Autostart: {4}\n" +
                                      "\nBundled Daemon Version: v{5}", 
                                      simName,
                                      CurrentDaemonInstallPath, 
                                      isInstalled ? "Yes (v" + installedVersion + ")" : "No", 
                                      isRunning ? "Yes (Running)" : "No (Stopped)", 
                                      isAutostart ? "Enabled" : "Disabled", 
                                      activeBundled);

                UpdateNavigationButtons();
                return;
            }

            if (string.IsNullOrEmpty(rf2Path) || !InstallManager.IsValidRF2Directory(rf2Path))
            {
                isInstalled = false;
                installedVersion = "";
                btnInstall.Enabled = false;

                lblAboutStatus.Text = "Status: NOT DETECTED\n\nPlease select your rFactor 2 installation folder in the first tab.";
                btnOpenConfig.Enabled = false;
                btnReinstall.Enabled = false;
                btnUninstall.Enabled = false;

                pnlInstallControls.Visible = false;
                pnlInstallError.Visible = true;

                btnNavUpdate.LabelText = "Update";
                UpdateNavigationButtons();
                return;
            }

            pnlInstallControls.Visible = true;
            pnlInstallError.Visible = false;

            btnInstall.Enabled = true;
            btnOpenConfig.Enabled = true;
            btnReinstall.Enabled = true;

            // Check if installed
            isInstalled = InstallManager.GetInstalledPluginDetails(rf2Path, out installedVersion);
            bool isActive = InstallManager.IsPluginActiveInGame(rf2Path);

            if (isInstalled)
            {
                btnUninstall.Enabled = true;
                bool isNewer = InstallManager.IsInstalledVersionNewer(installedVersion, InstallManager.BundledVersion);
                btnNavUpdate.LabelText = isNewer ? "Downgrade" : "Update";

                if (installedVersion != InstallManager.BundledVersion)
                {
                    btnInstall.Text = isNewer ? "Downgrade Telemetry Plugin" : "Update Telemetry Plugin";

                    lblUpdateHeader.Text = isNewer ? "Downgrade BrBrDb Telemetry" : "Update BrBrDb Telemetry";
                    btnUpdate.Text = isNewer ? "Downgrade" : "Update";
                    lblUpdateQuestion.Text = string.Format(
                        isNewer 
                            ? "A newer version of the plugin is installed (v{0}). Downgrade BrBrDbTelemetry to version {1}?" 
                            : "An older version of the plugin is installed (v{0}). Update BrBrDbTelemetry to version {1}?", 
                        installedVersion, 
                        InstallManager.BundledVersion
                    );
                }
                else
                {
                    btnInstall.Text = "Reinstall Telemetry Plugin";
                }

                // Load config for Editing
                LoadConfigFromFile();
            }
            else
            {
                btnInstall.Text = "Install Telemetry Plugin";
                btnUninstall.Enabled = false;
                btnNavUpdate.LabelText = "Update";
            }

            lblAboutStatus.Text = string.Format("Plugin Installation Status:\n" +
                                  "---------------------------\n" +
                                  "rFactor 2 Path: {0}\n" +
                                  "Installed Plugin: {1}\n" +
                                  "Enabled in Game: {2}\n" +
                                  "\nBundled Installer Plugin Version: v{3}", 
                                  rf2Path, 
                                  isInstalled ? "Yes (v" + installedVersion + ")" : "No", 
                                  isActive ? "Yes (Active)" : "No (Disabled)", 
                                  InstallManager.BundledVersion);

            UpdateNavigationButtons();
        }

        private void UpdateNavigationButtons()
        {
            bool autoPathError = (selectedSimulator == "rf2") && (string.IsNullOrEmpty(rf2Path) || !InstallManager.IsValidRF2Directory(rf2Path));

            string activeBundled = IsDaemonSimulator ? InstallManager.BundledAceVersion : InstallManager.BundledVersion;

            // Install tab is visible ONLY if path error OR not installed
            btnNavInstall.Visible = autoPathError || !isInstalled;

            // Update tab is visible ONLY if path is valid AND installed AND outdated
            btnNavUpdate.Visible = !autoPathError && isInstalled && (installedVersion != activeBundled);

            // Configuration tabs are visible ONLY if path is valid AND installed AND up-to-date
            bool configVisible = !autoPathError && isInstalled && (installedVersion == activeBundled);
            btnNavGeneral.Visible = configVisible;
            btnNavSinks.Visible = configVisible;
            btnNavOverrides.Visible = configVisible;
            btnNavSessions.Visible = configVisible;
            btnNavLogs.Visible = configVisible;
            btnNavManage.Visible = configVisible;

            UpdateDaemonStatusUI();

            // Reposition visible sidebar buttons below simulator dropdown and daemon status panel
            int startY = 134;
            foreach (var btn in navButtons)
            {
                if (btn.Visible)
                {
                    btn.Location = new Point(0, startY);
                    startY += 45;
                }
            }

            // Route to appropriate tab if the current one is hidden or not valid anymore
            if (btnNavInstall.Visible)
            {
                SwitchToTab("install");
            }
            else if (btnNavUpdate.Visible)
            {
                SwitchToTab("update");
            }
            else if (configVisible)
            {
                if (selectedTab == "install" || selectedTab == "update" || !IsTabAvailable(selectedTab))
                {
                    SwitchToTab("general");
                }
                else
                {
                    SwitchToTab(selectedTab);
                }
            }
        }

        private bool IsTabAvailable(string tabId)
        {
            foreach (var btn in navButtons)
            {
                if (btn.TabId == tabId)
                    return btn.Visible;
            }
            return false;
        }

        private void UpdateDaemonStatusUI()
        {
            if (pnlDaemonStatus != null)
                pnlDaemonStatus.Visible = true;

            bool isRunning = InstallManager.IsDaemonRunning();
            if (isRunning)
            {
                lblDaemonStatus.Text = "● Daemon: Running";
                lblDaemonStatus.ForeColor = ColorSuccess;
                btnDaemonToggle.Text = "⏹ Stop Daemon";
                btnDaemonToggle.BackColor = ColorDanger;
                btnDaemonToggle.FlatAppearance.MouseOverBackColor = Color.FromArgb(235, 111, 146);
            }
            else
            {
                lblDaemonStatus.Text = "○ Daemon: Stopped";
                lblDaemonStatus.ForeColor = ColorTextMuted;
                btnDaemonToggle.Text = "▶ Start Daemon";
                btnDaemonToggle.BackColor = ColorAccent;
                btnDaemonToggle.FlatAppearance.MouseOverBackColor = ColorAccentHover;
            }
        }

        private void BtnDaemonToggle_Click(object sender, EventArgs e)
        {
            if (InstallManager.IsDaemonRunning())
            {
                InstallManager.StopDaemon();
            }
            else
            {
                InstallManager.StartDaemon(CurrentDaemonInstallPath);
            }
            UpdateDaemonStatusUI();
        }

        private void LoadConfigFromFile()
        {
            string configPath = GetActiveConfigPath();
            if (File.Exists(configPath))
            {
                try
                {
                    string json = File.ReadAllText(configPath);
                    config = ConfigHelper.Deserialize(json);
                    
                    // Bind fields
                    if (IsDaemonSimulator)
                    {
                        txtCarRegexp.Text = config.GetCarRegexpForSimulator(selectedSimulator);
                        txtLeague.Text = config.GetLeagueForSimulator(selectedSimulator);
                    }
                    else
                    {
                        txtCarRegexp.Text = config.car_name_regexp;
                        txtLeague.Text = config.league;
                    }
                    txtKartNumberFilter.Text = config.kart_number_filter;
                    chkAllParticipantsInRace.Checked = config.all_participants_in_race;

                    // Bind install tab config from file if present
                    SinkConfig fileSink = config.sinks.Find(s => s.type == "file");
                    if (fileSink != null)
                        txtTelemetryPathInstall.Text = fileSink.path;
                    
                    SinkConfig httpSink = config.sinks.Find(s => s.type == "http");
                    if (httpSink != null)
                        txtApiKeyInstall.Text = httpSink.api_key;

                    RefreshSinksList();
                    RefreshOverridesList();
                }
                catch (Exception ex)
                {
                    MessageBox.Show("Error reading configuration JSON: " + ex.Message, "Config Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
        }

        private bool HasTabUnsavedChanges(string tabId)
        {
            string configPath = GetActiveConfigPath();
            if (!File.Exists(configPath))
                return false;

            try
            {
                string diskJson = File.ReadAllText(configPath);
                var diskConfig = ConfigHelper.Deserialize(diskJson);

                if (tabId == "general")
                {
                    string expectedCar = IsDaemonSimulator ? diskConfig.GetCarRegexpForSimulator(selectedSimulator) : diskConfig.car_name_regexp;
                    string expectedLeague = IsDaemonSimulator ? diskConfig.GetLeagueForSimulator(selectedSimulator) : diskConfig.league;
                    return txtCarRegexp.Text != expectedCar ||
                           txtKartNumberFilter.Text != diskConfig.kart_number_filter ||
                           txtLeague.Text != expectedLeague ||
                           chkAllParticipantsInRace.Checked != diskConfig.all_participants_in_race;
                }
                else if (tabId == "sinks")
                {
                    string currentSinksJson = ConfigHelper.Serialize(new PluginConfig { sinks = config.sinks });
                    string diskSinksJson = ConfigHelper.Serialize(new PluginConfig { sinks = diskConfig.sinks });
                    return currentSinksJson != diskSinksJson;
                }
                else if (tabId == "overrides")
                {
                    string currentOverridesJson = ConfigHelper.Serialize(new PluginConfig { driver_name_overrides = config.driver_name_overrides });
                    string diskOverridesJson = ConfigHelper.Serialize(new PluginConfig { driver_name_overrides = diskConfig.driver_name_overrides });
                    return currentOverridesJson != diskOverridesJson;
                }
            }
            catch
            {
                return false;
            }

            return false;
        }

        private void SwitchToTab(string tabId)
        {
            if (selectedTab != tabId && HasTabUnsavedChanges(selectedTab))
            {
                var result = MessageBox.Show(
                    "You have unsaved changes. Would you like to save them before leaving?",
                    "Unsaved Changes",
                    MessageBoxButtons.YesNoCancel,
                    MessageBoxIcon.Question
                );

                if (result == DialogResult.Yes)
                {
                    if (selectedTab == "general")
                    {
                        if (IsDaemonSimulator)
                        {
                            config.SetCarRegexpForSimulator(selectedSimulator, txtCarRegexp.Text);
                            config.SetLeagueForSimulator(selectedSimulator, txtLeague.Text);
                        }
                        else
                        {
                            config.car_name_regexp = txtCarRegexp.Text;
                            config.league = txtLeague.Text;
                        }
                        config.kart_number_filter = txtKartNumberFilter.Text;
                        config.all_participants_in_race = chkAllParticipantsInRace.Checked;
                    }
                    SaveConfigToDisk();
                }
                else if (result == DialogResult.Cancel)
                {
                    // Restore active nav buttons
                    foreach (var btn in navButtons)
                    {
                        btn.IsActive = (btn.TabId == selectedTab);
                    }
                    return;
                }
                else if (result == DialogResult.No)
                {
                    LoadConfigFromFile();
                }
            }

            selectedTab = tabId;

            // Update Nav buttons
            foreach (var btn in navButtons)
            {
                btn.IsActive = (btn.TabId == tabId);
            }

            // Hide all
            tabInstall.Visible = false;
            if (tabUpdate != null) tabUpdate.Visible = false;
            tabGeneral.Visible = false;
            tabSinks.Visible = false;
            tabOverrides.Visible = false;
            if (tabSessions != null) tabSessions.Visible = false;
            if (tabLogs != null) tabLogs.Visible = false;
            tabAbout.Visible = false;

            // Show current
            if (tabId == "install") tabInstall.Visible = true;
            else if (tabId == "update" && tabUpdate != null) tabUpdate.Visible = true;
            else if (tabId == "general") tabGeneral.Visible = true;
            else if (tabId == "sinks") tabSinks.Visible = true;
            else if (tabId == "overrides") tabOverrides.Visible = true;
            else if (tabId == "sessions" && tabSessions != null) { tabSessions.Visible = true; RefreshSessionsList(); }
            else if (tabId == "logs" && tabLogs != null) { tabLogs.Visible = true; RefreshLogsContent(); }
            else if (tabId == "manage") { tabAbout.Visible = true; TriggerGitHubUpdateCheck(); }
        }

        #region Install Events
        private void BrowseRf2Path_Click(object sender, EventArgs e)
        {
            using (var fbd = new FolderBrowserDialog())
            {
                if (IsDaemonSimulator)
                {
                    string simName = InstallManager.GetSimulatorDisplayName(selectedSimulator);
                    fbd.Description = "Select Folder to Install " + simName + " Telemetry Daemon";
                    fbd.SelectedPath = CurrentDaemonInstallPath;
                    if (fbd.ShowDialog() == DialogResult.OK)
                    {
                        CurrentDaemonInstallPath = fbd.SelectedPath;
                        txtRf2Path.Text = CurrentDaemonInstallPath;
                        RefreshInstallationState();
                    }
                }
                else
                {
                    fbd.Description = "Select your rFactor 2 Installation Folder";
                    if (fbd.ShowDialog() == DialogResult.OK)
                    {
                        if (InstallManager.IsValidRF2Directory(fbd.SelectedPath))
                        {
                            rf2Path = fbd.SelectedPath;
                            txtRf2Path.Text = rf2Path;
                            RefreshInstallationState();
                        }
                        else
                        {
                            MessageBox.Show("The selected folder is not a valid rFactor 2 installation. It must contain the 'Bin64\\Plugins' subfolder.", "Invalid Directory", MessageBoxButtons.OK, MessageBoxIcon.Error);
                        }
                    }
                }
            }
        }

        private void BrowseTelemetryPath_Click(object sender, EventArgs e)
        {
            using (var fbd = new FolderBrowserDialog())
            {
                fbd.Description = "Select Folder to Save Telemetry CSVs";
                if (fbd.ShowDialog() == DialogResult.OK)
                {
                    txtTelemetryPathInstall.Text = fbd.SelectedPath;
                }
            }
        }

        private void Install_Click(object sender, EventArgs e)
        {
            if (IsDaemonSimulator)
            {
                if (string.IsNullOrEmpty(txtApiKeyInstall.Text.Trim()))
                {
                    MessageBox.Show("API Key is required.", "Validation Error", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    return;
                }

                try
                {
                    InstallManager.InstallDaemonPlugin(
                        selectedSimulator,
                        CurrentDaemonInstallPath,
                        txtApiKeyInstall.Text.Trim(),
                        txtTelemetryPathInstall.Text.Trim(),
                        enableAutostart: true
                    );

                    string simName = InstallManager.GetSimulatorDisplayName(selectedSimulator);
                    MessageBox.Show(simName + " telemetry daemon installed, configured, and started!", "Success", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    RefreshInstallationState();
                }
                catch (Exception ex)
                {
                    MessageBox.Show("Installation failed:\n" + ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                return;
            }

            if (!InstallManager.IsValidRF2Directory(rf2Path))
            {
                MessageBox.Show("Please select a valid rFactor 2 directory first.", "Invalid Path", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            if (string.IsNullOrEmpty(txtApiKeyInstall.Text.Trim()))
            {
                MessageBox.Show("API Key is required.", "Validation Error", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            try
            {
                InstallManager.InstallPlugin(
                    rf2Path,
                    txtApiKeyInstall.Text.Trim(),
                    txtTelemetryPathInstall.Text.Trim()
                );

                MessageBox.Show("Plugin installed and configured successfully!", "Success", MessageBoxButtons.OK, MessageBoxIcon.Information);
                RefreshInstallationState();
            }
            catch (Exception ex)
            {
                MessageBox.Show("Installation failed:\n" + ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private void Update_Click(object sender, EventArgs e)
        {
            if (IsDaemonSimulator)
            {
                try
                {
                    bool isNewer = InstallManager.IsInstalledVersionNewer(installedVersion, InstallManager.BundledAceVersion);
                    InstallManager.InstallDaemonPlugin(selectedSimulator, CurrentDaemonInstallPath, "", "", enableAutostart: true);

                    string simName = InstallManager.GetSimulatorDisplayName(selectedSimulator);
                    string actionText = isNewer ? "downgraded" : "updated";
                    MessageBox.Show(simName + " daemon " + actionText + " successfully to version " + InstallManager.BundledAceVersion + "!", "Success", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    RefreshInstallationState();
                }
                catch (Exception ex)
                {
                    MessageBox.Show("Update failed:\n" + ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                return;
            }

            try
            {
                bool isNewer = InstallManager.IsInstalledVersionNewer(installedVersion, InstallManager.BundledVersion);
                InstallManager.InstallPlugin(rf2Path, "", "");

                string actionText = isNewer ? "downgraded" : "updated";
                MessageBox.Show("Plugin " + actionText + " successfully to version " + InstallManager.BundledVersion + "!", "Success", MessageBoxButtons.OK, MessageBoxIcon.Information);
                RefreshInstallationState();
            }
            catch (Exception ex)
            {
                MessageBox.Show("Update failed:\n" + ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private void Reinstall_Click(object sender, EventArgs e)
        {
            if (IsDaemonSimulator)
            {
                try
                {
                    InstallManager.InstallDaemonPlugin(selectedSimulator, CurrentDaemonInstallPath, "", "", enableAutostart: true);
                    string simName = InstallManager.GetSimulatorDisplayName(selectedSimulator);
                    MessageBox.Show(simName + " daemon reinstalled successfully!", "Success", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    RefreshInstallationState();
                }
                catch (Exception ex)
                {
                    MessageBox.Show("Reinstallation failed:\n" + ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                return;
            }

            try
            {
                InstallManager.InstallPlugin(rf2Path, "", "");

                MessageBox.Show("Plugin reinstalled successfully!", "Success", MessageBoxButtons.OK, MessageBoxIcon.Information);
                RefreshInstallationState();
            }
            catch (Exception ex)
            {
                MessageBox.Show("Reinstallation failed:\n" + ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
        #endregion

        #region General Settings Events
        private void SaveGeneral_Click(object sender, EventArgs e)
        {
            if (!isInstalled)
            {
                MessageBox.Show("Plugin must be installed to configure settings.", "Not Installed", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            if (IsDaemonSimulator)
            {
                config.SetCarRegexpForSimulator(selectedSimulator, txtCarRegexp.Text);
                config.SetLeagueForSimulator(selectedSimulator, txtLeague.Text);
            }
            else
            {
                config.car_name_regexp = txtCarRegexp.Text;
                config.league = txtLeague.Text;
            }
            config.kart_number_filter = txtKartNumberFilter.Text;
            config.all_participants_in_race = chkAllParticipantsInRace.Checked;

            bool restarted = SaveConfigToDisk();
            string msg = restarted
                ? "General settings saved successfully!\nTelemetry daemon was restarted with the new configuration."
                : "General settings saved successfully!";
            MessageBox.Show(msg, "Saved", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
        #endregion

        #region Sinks Events
        private void RefreshSinksList()
        {
            lstSinks.Items.Clear();
            for (int i = 0; i < config.sinks.Count; i++)
            {
                var s = config.sinks[i];
                lstSinks.Items.Add(string.Format("Sink {0}: {1} ({2})", i + 1, s.type, s.enabled ? "On" : "Off"));
            }
            if (config.sinks.Count > 0)
            {
                lstSinks.SelectedIndex = 0;
            }
            else
            {
                pnlSinkEdit.Visible = false;
            }
        }

        private void LstSinks_SelectedIndexChanged(object sender, EventArgs e)
        {
            int idx = lstSinks.SelectedIndex;
            if (idx >= 0 && idx < config.sinks.Count)
            {
                var s = config.sinks[idx];
                cbSinkType.SelectedItem = s.type;
                chkSinkEnabled.Checked = s.enabled;
                txtSinkPath.Text = s.path;
                txtSinkServer.Text = s.server_address;
                txtSinkApiKey.Text = s.api_key;
                pnlSinkEdit.Visible = true;
                UpdateSinkFieldVisibility(s.type);
            }
            else
            {
                pnlSinkEdit.Visible = false;
            }
        }

        private void UpdateSinkFieldVisibility(string type)
        {
            bool isFile = (type == "file");
            lblSinkPath.Visible = isFile;
            txtSinkPath.Visible = isFile;
            btnBrowseSinkPath.Visible = isFile;

            lblSinkServer.Visible = !isFile;
            txtSinkServer.Visible = !isFile;
            lblSinkServerHelp.Visible = !isFile;
            lblSinkApiKey.Visible = !isFile;
            txtSinkApiKey.Visible = !isFile;
            lblSinkApiKeyHelp.Visible = !isFile;
        }

        private void CbSinkType_SelectedIndexChanged(object sender, EventArgs e)
        {
            int idx = lstSinks.SelectedIndex;
            if (idx >= 0 && idx < config.sinks.Count)
            {
                string newType = cbSinkType.SelectedItem.ToString();
                config.sinks[idx].type = newType;
                UpdateSinkFieldVisibility(newType);
                lstSinks.Items[idx] = string.Format("Sink {0}: {1} ({2})", idx + 1, newType, config.sinks[idx].enabled ? "On" : "Off");
            }
        }

        private void ChkSinkEnabled_CheckedChanged(object sender, EventArgs e)
        {
            int idx = lstSinks.SelectedIndex;
            if (idx >= 0 && idx < config.sinks.Count)
            {
                config.sinks[idx].enabled = chkSinkEnabled.Checked;
                lstSinks.Items[idx] = string.Format("Sink {0}: {1} ({2})", idx + 1, config.sinks[idx].type, chkSinkEnabled.Checked ? "On" : "Off");
            }
        }

        private void TxtSinkPath_TextChanged(object sender, EventArgs e)
        {
            int idx = lstSinks.SelectedIndex;
            if (idx >= 0 && idx < config.sinks.Count)
                config.sinks[idx].path = txtSinkPath.Text;
        }

        private void TxtSinkServer_TextChanged(object sender, EventArgs e)
        {
            int idx = lstSinks.SelectedIndex;
            if (idx >= 0 && idx < config.sinks.Count)
                config.sinks[idx].server_address = txtSinkServer.Text;
        }

        private void TxtSinkApiKey_TextChanged(object sender, EventArgs e)
        {
            int idx = lstSinks.SelectedIndex;
            if (idx >= 0 && idx < config.sinks.Count)
                config.sinks[idx].api_key = txtSinkApiKey.Text;
        }

        private void BrowseSinkPath_Click(object sender, EventArgs e)
        {
            using (var fbd = new FolderBrowserDialog())
            {
                fbd.Description = "Select Sink Output Folder";
                if (fbd.ShowDialog() == DialogResult.OK)
                {
                    txtSinkPath.Text = fbd.SelectedPath;
                }
            }
        }

        private void AddSink_Click(object sender, EventArgs e)
        {
            var newSink = new SinkConfig { type = "file", enabled = true };
            config.sinks.Add(newSink);
            RefreshSinksList();
            lstSinks.SelectedIndex = config.sinks.Count - 1;
        }

        private void DeleteSink_Click(object sender, EventArgs e)
        {
            int idx = lstSinks.SelectedIndex;
            if (idx >= 0 && idx < config.sinks.Count)
            {
                config.sinks.RemoveAt(idx);
                RefreshSinksList();
            }
        }

        private void SaveSinks_Click(object sender, EventArgs e)
        {
            if (!isInstalled)
            {
                MessageBox.Show("Plugin must be installed to save configuration.", "Not Installed", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            bool restarted = SaveConfigToDisk();
            string msg = restarted
                ? "Sinks configuration saved successfully!\nTelemetry daemon was restarted with the new configuration."
                : "Sinks configuration saved successfully!";
            MessageBox.Show(msg, "Saved", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
        #endregion

        #region Overrides Events
        private void RefreshOverridesList()
        {
            lstOverrides.Items.Clear();
            foreach (var over in config.driver_name_overrides)
            {
                lstOverrides.Items.Add(string.Format("\"{0}\" ➔ {1}", over.car_name_regexp, over.driver_name));
            }
            if (config.driver_name_overrides.Count > 0)
            {
                lstOverrides.SelectedIndex = 0;
            }
            else
            {
                pnlOverrideEdit.Visible = false;
            }
        }

        private void LstOverrides_SelectedIndexChanged(object sender, EventArgs e)
        {
            int idx = lstOverrides.SelectedIndex;
            if (idx >= 0 && idx < config.driver_name_overrides.Count)
            {
                var over = config.driver_name_overrides[idx];
                txtOverrideCarReg.TextChanged -= TxtOverrideCarReg_TextChanged;
                txtOverrideDriver.TextChanged -= TxtOverrideDriver_TextChanged;

                txtOverrideCarReg.Text = over.car_name_regexp;
                txtOverrideDriver.Text = over.driver_name;

                txtOverrideCarReg.TextChanged += TxtOverrideCarReg_TextChanged;
                txtOverrideDriver.TextChanged += TxtOverrideDriver_TextChanged;

                pnlOverrideEdit.Visible = true;
            }
            else
            {
                pnlOverrideEdit.Visible = false;
            }
        }

        private void TxtOverrideCarReg_TextChanged(object sender, EventArgs e)
        {
            int idx = lstOverrides.SelectedIndex;
            if (idx >= 0 && idx < config.driver_name_overrides.Count)
                config.driver_name_overrides[idx].car_name_regexp = txtOverrideCarReg.Text;
        }

        private void TxtOverrideDriver_TextChanged(object sender, EventArgs e)
        {
            int idx = lstOverrides.SelectedIndex;
            if (idx >= 0 && idx < config.driver_name_overrides.Count)
                config.driver_name_overrides[idx].driver_name = txtOverrideDriver.Text;
        }

        private void TxtOverrideFields_Leave(object sender, EventArgs e)
        {
            // Update listbox label only when focus leaves the field, not while typing.
            // Updating Items[idx] during TextChanged causes SelectedIndexChanged to fire
            // briefly with idx=-1, hiding pnlOverrideEdit and stealing focus back to
            // txtOverrideCarReg (the first focusable control in the panel).
            int idx = lstOverrides.SelectedIndex;
            if (idx >= 0 && idx < config.driver_name_overrides.Count)
                lstOverrides.Items[idx] = string.Format("\"{0}\" ➔ {1}",
                    config.driver_name_overrides[idx].car_name_regexp,
                    config.driver_name_overrides[idx].driver_name);
        }

        private void AddOverride_Click(object sender, EventArgs e)
        {
            var newOver = new DriverNameOverride { car_name_regexp = ".*", driver_name = "New Driver" };
            config.driver_name_overrides.Add(newOver);
            RefreshOverridesList();
            lstOverrides.SelectedIndex = config.driver_name_overrides.Count - 1;
        }

        private void DeleteOverride_Click(object sender, EventArgs e)
        {
            int idx = lstOverrides.SelectedIndex;
            if (idx >= 0 && idx < config.driver_name_overrides.Count)
            {
                config.driver_name_overrides.RemoveAt(idx);
                RefreshOverridesList();
            }
        }

        private void SaveOverrides_Click(object sender, EventArgs e)
        {
            if (!isInstalled)
            {
                MessageBox.Show("Plugin must be installed to save configuration.", "Not Installed", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            bool restarted = SaveConfigToDisk();
            string msg = restarted
                ? "Driver Overrides configuration saved successfully!\nTelemetry daemon was restarted with the new configuration."
                : "Driver Overrides configuration saved successfully!";
            MessageBox.Show(msg, "Saved", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
        #endregion

        #region About/Uninstall Events
        private void OpenConfig_Click(object sender, EventArgs e)
        {
            string configPath = GetActiveConfigPath();
            if (File.Exists(configPath))
            {
                try
                {
                    System.Diagnostics.Process.Start("notepad.exe", configPath);
                }
                catch (Exception ex)
                {
                    MessageBox.Show("Failed to open configuration file: " + ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
            else
            {
                MessageBox.Show("Configuration file does not exist. Please complete installation first.", "File Not Found", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        private void ViewRelease_Click(object sender, EventArgs e)
        {
            if (cachedReleaseInfo != null && !string.IsNullOrEmpty(cachedReleaseInfo.HtmlUrl))
            {
                try
                {
                    System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(cachedReleaseInfo.HtmlUrl)
                    {
                        UseShellExecute = true
                    });
                }
                catch (Exception ex)
                {
                    MessageBox.Show("Could not open browser: " + ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
        }

        private async void TriggerGitHubUpdateCheck(bool forceRefresh = false)
        {
            if (isCheckingForUpdates)
                return;

            if (!forceRefresh && cachedReleaseInfo != null)
            {
                UpdateGitHubReleaseUI(cachedReleaseInfo);
                return;
            }

            isCheckingForUpdates = true;
            btnCheckUpdates.Enabled = false;
            lblUpdateStatus.ForeColor = ColorTextMuted;
            lblUpdateStatus.Text = "Querying GitHub for latest release...";

            string activeBundled = IsDaemonSimulator ? InstallManager.BundledAceVersion : InstallManager.BundledVersion;

            try
            {
                var release = await InstallManager.CheckForGitHubUpdateAsync(activeBundled);
                cachedReleaseInfo = release;
                if (!this.IsDisposed)
                {
                    UpdateGitHubReleaseUI(release);
                }
            }
            catch (Exception ex)
            {
                if (!this.IsDisposed)
                {
                    lblUpdateStatus.ForeColor = ColorWarning;
                    lblUpdateStatus.Text = "Update check failed: " + ex.Message;
                }
            }
            finally
            {
                if (!this.IsDisposed)
                {
                    isCheckingForUpdates = false;
                    btnCheckUpdates.Enabled = true;
                }
            }
        }

        private void UpdateGitHubReleaseUI(InstallManager.GitHubReleaseInfo release)
        {
            if (release == null)
                return;

            string activeBundled = IsDaemonSimulator ? InstallManager.BundledAceVersion : InstallManager.BundledVersion;

            if (!release.Success)
            {
                lblUpdateStatus.ForeColor = ColorWarning;
                lblUpdateStatus.Text = string.IsNullOrEmpty(release.ErrorMessage)
                    ? "Unable to retrieve GitHub release status."
                    : release.ErrorMessage;
                btnViewRelease.Visible = false;
                return;
            }

            if (release.IsNewer)
            {
                lblUpdateStatus.ForeColor = ColorAccentHover;
                string dateStr = release.PublishedAt.HasValue ? string.Format(" ({0})", release.PublishedAt.Value.ToString("MMM d, yyyy")) : "";
                lblUpdateStatus.Text = string.Format("New release available: {0}{1}!\nCurrent version: v{2}", 
                    release.TagName, dateStr, activeBundled);
                btnViewRelease.Visible = true;
                btnViewRelease.Text = "View " + release.TagName;
            }
            else
            {
                lblUpdateStatus.ForeColor = ColorSuccess;
                lblUpdateStatus.Text = string.Format("You have the latest version (v{0}).\nGitHub release: {1}", 
                    activeBundled, release.TagName);
                btnViewRelease.Visible = true;
                btnViewRelease.Text = "View on GitHub";
            }
        }

        private void Uninstall_Click(object sender, EventArgs e)
        {
            string simName = IsDaemonSimulator ? (InstallManager.GetSimulatorDisplayName(selectedSimulator) + " telemetry daemon") : "rFactor 2 telemetry plugin";
            if (MessageBox.Show(string.Format("Are you sure you want to completely uninstall the {0} and delete its configuration?", simName), "Confirm Uninstallation", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) == DialogResult.Yes)
            {
                try
                {
                    if (IsDaemonSimulator)
                    {
                        InstallManager.UninstallDaemonPlugin(selectedSimulator, CurrentDaemonInstallPath);
                    }
                    else
                    {
                        InstallManager.UninstallPlugin(rf2Path);
                    }
                    MessageBox.Show(simName + " successfully uninstalled.", "Success", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    RefreshInstallationState();
                }
                catch (Exception ex)
                {
                    MessageBox.Show("Uninstallation failed:\n" + ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
        }
        #endregion

        private bool SaveConfigToDisk()
        {
            try
            {
                string configPath = GetActiveConfigPath();
                string dir = Path.GetDirectoryName(configPath);
                if (!Directory.Exists(dir))
                    Directory.CreateDirectory(dir);
                string configJson = ConfigHelper.Serialize(config);
                File.WriteAllText(configPath, configJson, Encoding.UTF8);

                // If the telemetry daemon is currently running,
                // restart the daemon so the newly saved configuration takes effect immediately.
                if (InstallManager.IsDaemonRunning())
                {
                    InstallManager.RestartDaemon(CurrentDaemonInstallPath);
                    UpdateDaemonStatusUI();
                    return true;
                }
                return false;
            }
            catch (Exception ex)
            {
                MessageBox.Show("Failed to save config: " + ex.Message, "Save Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return false;
            }
        }

        #region Dragging Chrome
        private void TitleBar_MouseDown(object sender, MouseEventArgs e)
        {
            isDragging = true;
            dragCursorPoint = Cursor.Position;
            dragFormPoint = this.Location;
        }

        private void TitleBar_MouseMove(object sender, MouseEventArgs e)
        {
            if (isDragging)
            {
                Point dif = Point.Subtract(Cursor.Position, new Size(dragCursorPoint));
                this.Location = Point.Add(dragFormPoint, new Size(dif));
            }
        }

        private void TitleBar_MouseUp(object sender, MouseEventArgs e)
        {
            isDragging = false;
        }
        #endregion
    }

    #region Custom Controls (For aesthetics)
    public class FlatButton : Button
    {
        private static readonly Color ColorAccent = Color.FromArgb(136, 57, 239);
        private static readonly Color ColorAccentHover = Color.FromArgb(166, 129, 246);
        private Color baseBackColor = ColorAccent;
        private bool isHovered = false;

        public FlatButton(string text, int width, int height)
        {
            this.Text = text;
            this.Width = width;
            this.Height = height;
            this.FlatStyle = FlatStyle.Flat;
            this.FlatAppearance.BorderSize = 0;
            this.BackColor = ColorAccent;
            this.baseBackColor = ColorAccent;
            this.ForeColor = Color.White;
            this.Font = new Font("Segoe UI", 9.5f, FontStyle.Bold);
            this.Cursor = Cursors.Hand;
        }

        protected override void OnBackColorChanged(EventArgs e)
        {
            base.OnBackColorChanged(e);
            if (!isHovered)
            {
                baseBackColor = this.BackColor;
            }
        }

        protected override void OnMouseEnter(EventArgs e)
        {
            isHovered = true;
            if (baseBackColor == ColorAccent)
            {
                this.BackColor = ColorAccentHover;
            }
            else
            {
                this.BackColor = Color.FromArgb(
                    Math.Min(255, baseBackColor.R + 25),
                    Math.Min(255, baseBackColor.G + 25),
                    Math.Min(255, baseBackColor.B + 25));
            }
            base.OnMouseEnter(e);
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            isHovered = false;
            this.BackColor = baseBackColor;
            base.OnMouseLeave(e);
        }
    }

    public class NavButton : Control
    {
        public string TabId { get; private set; }
        
        private string labelText;
        public string LabelText
        {
            get { return labelText; }
            set { labelText = value; Invalidate(); }
        }
        
        private bool isActive = false;
        public bool IsActive
        {
            get { return isActive; }
            set { isActive = value; Invalidate(); }
        }

        private bool isHovered = false;

        public NavButton(string id, string text)
        {
            this.TabId = id;
            this.LabelText = text;
            this.Cursor = Cursors.Hand;
            this.DoubleBuffered = true;
        }

        protected override void OnMouseEnter(EventArgs e)
        {
            isHovered = true;
            Invalidate();
            base.OnMouseEnter(e);
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            isHovered = false;
            Invalidate();
            base.OnMouseLeave(e);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;

            // Draw Background
            Color bg = Color.FromArgb(24, 24, 37); // normal sidebar bg
            if (isActive)
                bg = Color.FromArgb(49, 50, 68); // active
            else if (isHovered)
                bg = Color.FromArgb(37, 37, 54); // hover

            using (var brush = new SolidBrush(bg))
            {
                g.FillRectangle(brush, this.ClientRectangle);
            }

            // Draw Active Indicator Line
            if (isActive)
            {
                using (var brush = new SolidBrush(Color.FromArgb(136, 57, 239)))
                {
                    g.FillRectangle(brush, 0, 0, 5, this.Height);
                }
            }

            // Draw Text
            using (var brush = new SolidBrush(isActive ? Color.White : Color.FromArgb(205, 214, 244)))
            {
                var sf = new StringFormat
                {
                    LineAlignment = StringAlignment.Center,
                    Alignment = StringAlignment.Near
                };
                g.DrawString(LabelText, new Font("Segoe UI", 9.5f, isActive ? FontStyle.Bold : FontStyle.Regular), brush, new Rectangle(15, 0, this.Width - 15, this.Height), sf);
            }
        }
    }

    public class CircleHelpButton : Control
    {
        private bool isHovered = false;

        public CircleHelpButton()
        {
            this.Size = new Size(16, 16);
            this.Cursor = Cursors.Hand;
            this.DoubleBuffered = true;
        }

        protected override void OnMouseEnter(EventArgs e)
        {
            base.OnMouseEnter(e);
            isHovered = true;
            Invalidate();
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            base.OnMouseLeave(e);
            isHovered = false;
            Invalidate();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;

            Color circleColor = isHovered ? Color.FromArgb(166, 129, 246) : Color.FromArgb(166, 173, 200);
            using (var pen = new Pen(circleColor, 1.2f))
            {
                g.DrawEllipse(pen, 1, 1, Width - 3, Height - 3);
            }

            using (var font = new Font("Segoe UI", 8.0f, FontStyle.Bold))
            using (var brush = new SolidBrush(circleColor))
            using (var sf = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
            {
                g.DrawString("?", font, brush, new RectangleF(0, 0, Width, Height), sf);
            }
        }
    }
    #endregion
}
