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
        private string rf2Path = "";
        private PluginConfig config = new PluginConfig();
        private bool isInstalled = false;
        private string installedVersion = "";
        private string selectedTab = "install";

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
        private FlatButton btnOpenConfig;
        private FlatButton btnReinstall;
        private FlatButton btnUninstall;

        public InstallerForm()
        {
            this.Size = new Size(820, 520);
            this.FormBorderStyle = FormBorderStyle.None;
            this.BackColor = ColorBg;
            this.ForeColor = ColorText;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.Text = "BrBrDb Telemetry";

            InitializeWindowChrome();
            InitializeSidebar();
            InitializeContentArea();

            // Auto-detect rFactor 2 path
            rf2Path = InstallManager.DetectRFactor2Path();
            txtRf2Path.Text = rf2Path;
        }

        protected override void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
            RefreshInstallationState();
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

            lblTitle = new Label
            {
                Text = " BrBrDb Telemetry",
                Font = new Font("Segoe UI", 10, FontStyle.Bold),
                ForeColor = ColorText,
                AutoSize = true,
                Location = new Point(12, 10)
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

            CreateHeaderLabel(tabInstall, "Install BrBrTelemetry v" + InstallManager.BundledVersion, 20);

            // 1. Controls Panel (for normal installation)
            pnlInstallControls = new Panel
            {
                Location = new Point(0, 60),
                Size = new Size(tabInstall.Width, tabInstall.Height - 60),
                BackColor = ColorBg
            };
            tabInstall.Controls.Add(pnlInstallControls);

            // Path to rFactor 2
            CreateFieldLabel(pnlInstallControls, "rFactor 2 Installation Path:", 5);
            txtRf2Path = CreateTextBox(pnlInstallControls, 30, 580);
            txtRf2Path.ReadOnly = true;
            btnBrowsePath = new FlatButton("Browse...", 110, 25) { Location = new Point(500, 30), Visible = false };
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

            btnSaveGeneral = new FlatButton("Save Configuration", 200, 40)
            {
                Location = new Point(20, 370),
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
                Location = new Point(20, 70),
                Size = new Size(580, 160),
                Font = new Font("Segoe UI", 10),
                ForeColor = ColorText,
                Text = "Loading details..."
            };
            tabAbout.Controls.Add(lblAboutStatus);

            btnOpenConfig = new FlatButton("Open Configuration File", 580, 40)
            {
                Location = new Point(20, 250),
                BackColor = ColorInputBg
            };
            btnOpenConfig.Click += OpenConfig_Click;
            tabAbout.Controls.Add(btnOpenConfig);

            btnReinstall = new FlatButton("Reinstall BrBrDb Telemetry", 580, 40)
            {
                Location = new Point(20, 310),
                BackColor = ColorAccent
            };
            btnReinstall.Click += Reinstall_Click;
            tabAbout.Controls.Add(btnReinstall);

            btnUninstall = new FlatButton("Uninstall BrBrDb Telemetry", 580, 40)
            {
                Location = new Point(20, 370),
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
                MultiSelect = false,
                HeaderStyle = ColumnHeaderStyle.Nonclickable,
                BackColor = ColorSidebar,
                ForeColor = ColorText,
                BorderStyle = BorderStyle.FixedSingle,
                Font = new Font("Segoe UI", 9.5f)
            };
            lvSessions.Columns.Add("Date", 100);
            lvSessions.Columns.Add("Time", 80);
            lvSessions.Columns.Add("Track", 280);
            lvSessions.Columns.Add("Size", 95);
            tabSessions.Controls.Add(lvSessions);

            btnReuploadSession = new FlatButton("Re-upload Session", 180, 36)
            {
                Location = new Point(20, 380),
                BackColor = ColorAccent
            };
            btnReuploadSession.Click += ReuploadSession_Click;
            tabSessions.Controls.Add(btnReuploadSession);

            btnOpenSessionsDir = new FlatButton("Open in File Manager", 180, 36)
            {
                Location = new Point(210, 380),
                BackColor = ColorInputBg
            };
            btnOpenSessionsDir.Click += OpenSessionsDir_Click;
            tabSessions.Controls.Add(btnOpenSessionsDir);

            btnRefreshSessions = new FlatButton("Refresh List", 140, 36)
            {
                Location = new Point(400, 380),
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

        private void RefreshSessionsList()
        {
            lvSessions.Items.Clear();

            string dirPath = InstallManager.GetSessionsDirectoryPath(rf2Path, config);
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
        }

        private void RefreshLogsContent()
        {
            string logPath = InstallManager.GetLogFilePath(rf2Path);
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
            string dirPath = InstallManager.GetSessionsDirectoryPath(rf2Path, config);
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
                MessageBox.Show("Please select a session from the table to re-upload.", "No Session Selected", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }

            var meta = lvSessions.SelectedItems[0].Tag as InstallManager.SessionMetadata;
            if (meta == null || meta.FileInfo == null || !meta.FileInfo.Exists)
            {
                MessageBox.Show("Selected session file could not be found on disk.", "File Missing", MessageBoxButtons.OK, MessageBoxIcon.Warning);
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

            var confirmResult = MessageBox.Show(
                string.Format("Re-upload session for track '{0}' ({1} {2}, {3:F2} MB) to {4}?",
                    meta.Track, meta.Date, meta.Time, meta.SizeMb, serverAddress),
                "Confirm Re-upload",
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Question);

            if (confirmResult != DialogResult.Yes)
                return;

            this.Cursor = Cursors.WaitCursor;
            btnReuploadSession.Enabled = false;
            lblSessionsStatus.Text = "Uploading session '" + meta.Track + "' to " + serverAddress + "...";
            this.Refresh();

            string errorMsg;
            bool success = InstallManager.UploadSessionFile(meta.FileInfo.FullName, serverAddress, apiKey, out errorMsg);

            this.Cursor = Cursors.Default;
            btnReuploadSession.Enabled = true;
            lblSessionsStatus.Text = "Directory: " + InstallManager.GetSessionsDirectoryPath(rf2Path, config);

            if (success)
            {
                MessageBox.Show(
                    string.Format("Successfully re-uploaded telemetry session for '{0}' to {1}!", meta.Track, serverAddress),
                    "Upload Successful",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
            }
            else
            {
                MessageBox.Show(
                    string.Format("Failed to re-upload session to {0}.\n\nError details: {1}", serverAddress, errorMsg),
                    "Upload Failed",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
            }
        }

        private void OpenLogExternal_Click(object sender, EventArgs e)
        {
            string logPath = InstallManager.GetLogFilePath(rf2Path);
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
            bool autoPathError = string.IsNullOrEmpty(rf2Path) || !InstallManager.IsValidRF2Directory(rf2Path);

            // Install tab is visible ONLY if path error OR not installed
            btnNavInstall.Visible = autoPathError || !isInstalled;

            // Update tab is visible ONLY if path is valid AND installed AND outdated
            btnNavUpdate.Visible = !autoPathError && isInstalled && (installedVersion != InstallManager.BundledVersion);

            // Configuration tabs are visible ONLY if path is valid AND installed AND up-to-date
            bool configVisible = !autoPathError && isInstalled && (installedVersion == InstallManager.BundledVersion);
            btnNavGeneral.Visible = configVisible;
            btnNavSinks.Visible = configVisible;
            btnNavOverrides.Visible = configVisible;
            btnNavSessions.Visible = configVisible;
            btnNavLogs.Visible = configVisible;
            btnNavManage.Visible = configVisible;

            // Reposition visible sidebar buttons
            int startY = 20;
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

        private void LoadConfigFromFile()
        {
            string configPath = Path.Combine(rf2Path, @"Bin64\Plugins\BrBrDbTelemetry.json");
            if (File.Exists(configPath))
            {
                try
                {
                    string json = File.ReadAllText(configPath);
                    config = ConfigHelper.Deserialize(json);
                    
                    // Bind fields
                    txtCarRegexp.Text = config.car_name_regexp;
                    txtKartNumberFilter.Text = config.kart_number_filter;
                    txtLeague.Text = config.league;

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
            if (string.IsNullOrEmpty(rf2Path) || !InstallManager.IsValidRF2Directory(rf2Path))
                return false;

            string configPath = Path.Combine(rf2Path, @"Bin64\Plugins\BrBrDbTelemetry.json");
            if (!File.Exists(configPath))
                return false;

            try
            {
                string diskJson = File.ReadAllText(configPath);
                var diskConfig = ConfigHelper.Deserialize(diskJson);

                if (tabId == "general")
                {
                    return txtCarRegexp.Text != diskConfig.car_name_regexp ||
                           txtKartNumberFilter.Text != diskConfig.kart_number_filter ||
                           txtLeague.Text != diskConfig.league;
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
                        config.car_name_regexp = txtCarRegexp.Text;
                        config.kart_number_filter = txtKartNumberFilter.Text;
                        config.league = txtLeague.Text;
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
            else if (tabId == "manage") tabAbout.Visible = true;
        }

        #region Install Events
        private void BrowseRf2Path_Click(object sender, EventArgs e)
        {
            using (var fbd = new FolderBrowserDialog())
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

            config.car_name_regexp = txtCarRegexp.Text;
            config.kart_number_filter = txtKartNumberFilter.Text;
            config.league = txtLeague.Text;

            SaveConfigToDisk();
            MessageBox.Show("General settings saved successfully!", "Saved", MessageBoxButtons.OK, MessageBoxIcon.Information);
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
            SaveConfigToDisk();
            MessageBox.Show("Sinks configuration saved successfully!", "Saved", MessageBoxButtons.OK, MessageBoxIcon.Information);
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
            SaveConfigToDisk();
            MessageBox.Show("Driver Overrides configuration saved successfully!", "Saved", MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
        #endregion

        #region About/Uninstall Events
        private void OpenConfig_Click(object sender, EventArgs e)
        {
            string configPath = Path.Combine(rf2Path, @"Bin64\Plugins\BrBrDbTelemetry.json");
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

        private void Uninstall_Click(object sender, EventArgs e)
        {
            if (MessageBox.Show("Are you sure you want to completely uninstall the BrBrDb Telemetry plugin and delete its configuration?", "Confirm Uninstallation", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) == DialogResult.Yes)
            {
                try
                {
                    InstallManager.UninstallPlugin(rf2Path);
                    MessageBox.Show("Plugin successfully uninstalled.", "Success", MessageBoxButtons.OK, MessageBoxIcon.Information);
                    RefreshInstallationState();
                }
                catch (Exception ex)
                {
                    MessageBox.Show("Uninstallation failed:\n" + ex.Message, "Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
            }
        }
        #endregion

        private void SaveConfigToDisk()
        {
            try
            {
                string configPath = Path.Combine(rf2Path, @"Bin64\Plugins\BrBrDbTelemetry.json");
                string configJson = ConfigHelper.Serialize(config);
                File.WriteAllText(configPath, configJson, Encoding.UTF8);
            }
            catch (Exception ex)
            {
                MessageBox.Show("Failed to save config: " + ex.Message, "Save Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
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

        public FlatButton(string text, int width, int height)
        {
            this.Text = text;
            this.Width = width;
            this.Height = height;
            this.FlatStyle = FlatStyle.Flat;
            this.FlatAppearance.BorderSize = 0;
            this.BackColor = ColorAccent;
            this.ForeColor = Color.White;
            this.Font = new Font("Segoe UI", 9.5f, FontStyle.Bold);
            this.Cursor = Cursors.Hand;
        }

        protected override void OnMouseEnter(EventArgs e)
        {
            this.BackColor = ColorAccentHover;
            base.OnMouseEnter(e);
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            this.BackColor = ColorAccent;
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
    #endregion
}
