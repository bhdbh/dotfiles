require("git"):setup {
    order = 1500,
}

require("zoxide"):setup {
    update_db = true,
}

-- Shared dialogs for the file chooser portal and applications.
require("desktop-dialog"):setup()
