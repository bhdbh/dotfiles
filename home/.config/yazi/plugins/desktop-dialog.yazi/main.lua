--- @sync entry

local M = {}

local function notify(err)
  ya.notify { title = "Yazi / Dialogs", content = tostring(err), level = "error", timeout = 15 }
end

local function read_request()
  local name = os.getenv("YAZI_DIALOG_REQUEST_FILE") or os.getenv("YAZI_VSCODE_REQUEST_FILE")
  local file = name and io.open(name, "r")
  if not file then return end
  local raw = file:read("*a")
  file:close()
  -- Consume once: nested Yazi sessions must never repeat a destructive request.
  if not os.remove(name) then return nil, "Could not consume the dialog request." end
  local req, err = ya.json_decode(raw)
  if not req then return nil, err end
  if type(req) ~= "table" then return nil, "Invalid dialog request." end
  if (req.action ~= "trash" and req.action ~= "delete" and req.action ~= "manage")
    or type(req.cwd) ~= "string" or req.cwd:sub(1, 1) ~= "/"
    or type(req.targets) ~= "table" or #req.targets == 0 then
    return nil, "Invalid dialog request. No operation was performed."
  end
  for _, target in ipairs(req.targets) do
    if type(target) ~= "string" or target:sub(1, 1) ~= "/" then
      return nil, "The selection must contain only absolute local paths."
    end
  end
  return req
end

local current = ya.sync(function(_, cwd, hovered)
  local folder = cx.active.current
  return tostring(folder.cwd) == cwd
    and (not hovered or (folder.hovered and tostring(folder.hovered.url) == hovered))
end)

local selection_count = ya.sync(function() return #cx.active.selected end)

local can_close = ya.sync(function()
  local tasks = cx.tasks.summary
  return tostring(cx.layer) == "mgr" and tasks.failed == 0 and tasks.total == tasks.success
end)

local refresh_progress = ya.sync(function(self)
  local progress = self.progress
  if not progress or not progress.active or progress.finished then return true end
  local tasks = cx.tasks.summary
  local percent = progress.completed / progress.total * 100
  -- Trash is atomic per selected item. A single permanent-deletion task also
  -- exposes byte-based progress, useful when the selected item is a directory.
  if progress.action == "delete" and progress.total == 1 and tasks.percent then
    percent = math.max(percent, tasks.percent)
  end
  progress.percent = math.min(99, math.max(progress.percent, math.floor(percent)))
  progress.failed = tasks.failed > 0
  ui.render()
  return false
end)

local finish_progress = ya.sync(function(self)
  self.progress.percent, self.progress.finished = 100, true
  ui.render()
end)

local arm_completion = ya.sync(function(self, req)
  local pending, remaining, closing = {}, 0, false
  for _, target in ipairs(req.targets) do
    if not pending[target] then pending[target], remaining = true, remaining + 1 end
  end
  ps.sub(req.action, function(body)
    for _, url in ipairs(body.urls) do
      local target = tostring(url)
      if pending[target] then
        pending[target], remaining = nil, remaining - 1
        self.progress.completed = self.progress.completed + 1
      end
    end
    if remaining ~= 0 or closing then return end
    closing = true
    ya.async(function()
      -- A success event can precede the scheduler's final task cleanup.
      while not can_close() do ya.sleep(0.05) end
      finish_progress()
      -- Let the final, truthful 100% frame appear before the window disappears.
      ya.sleep(0.2)
      ya.exec("quit", { no_cwd_file = true })
    end)
  end)
end)

local function wait_for(cwd, hovered)
  for _ = 1, 100 do
    if current(cwd, hovered) then return end
    ya.sleep(0.05)
  end
  error("Could not locate the requested selection in Yazi.")
end

local function select_files(targets)
  local files = {}
  for _, target in ipairs(targets) do
    local file, err = fs.file(Url(target))
    if not file then error("Could not access " .. target .. ": " .. tostring(err)) end
    files[#files + 1] = file
  end
  ya.exec("escape", { select = true })
  for _, file in ipairs(files) do ya.exec("toggle", { file, state = "on" }) end
  if selection_count() ~= #targets then
    error("Could not reproduce the selection. No operation was performed.")
  end
end

local function progress_widgets(area, progress)
  if not progress.active then return {} end
  local title = progress.finished and "Done"
    or progress.failed and "Deletion failed"
    or progress.action == "trash" and "Moving to Trash..." or "Deleting..."
  local percent = string.format("%d%%", progress.percent)
  local style = progress.failed and th.status.progress_error or th.status.progress_normal
  if area.w < 6 or area.h < 4 then
    return { ui.Gauge():area(area):percent(progress.percent):gauge_style(style):label(percent) }
  end
  local width = math.min(64, area.w - 4)
  local x = area.x + math.floor((area.w - width) / 2)
  local y = area.y + math.floor((area.h - 4) / 2)
  return {
    ui.Text(title):area(ui.Rect { x = x, y = y, w = width, h = 1 }):align(ui.Align.CENTER),
    ui.Gauge():area(ui.Rect { x = x, y = y + 1, w = width, h = 1 })
      :percent(progress.percent):gauge_style(style)
      :label(ui.Span(percent):style(th.status.progress_label)),
    ui.Text(string.format("%d / %d", progress.completed, progress.total))
      :area(ui.Rect { x = x, y = y + 3, w = width, h = 1 }):align(ui.Align.CENTER),
  }
end

local function confirmation_only(progress)
  -- The native confirmation is rendered independently of Root. Disable the
  -- preview too, so neither text nor image previewers expose the browser.
  rt.mgr.ratio = { 0, 1, 0 }
  function Root:redraw()
    return ya.list_merge({ ui.Fill(self._area):style(th.app.overall) }, progress_widgets(self._area, progress))
  end
  -- The centered progress replaces the regular status bar in this process.
  function Progress:redraw() return {} end
  for _, event in ipairs { "click", "scroll", "touch", "drag", "drop", "clipboard" } do
    Root[event] = function() end
  end
  -- The browser remains an implementation detail, never an invisible target
  -- for file-management shortcuts while the action starts or finishes.
  for _, chord in pairs(km.mgr.rules:match()) do km.mgr.rules:remove { id = chord.id } end
  for _, key in ipairs { "<Esc>", "<C-[>", "<C-c>", "q" } do
    km.mgr.rules:insert(1, { on = key, run = "plugin desktop-dialog" })
  end
  for _, key in ipairs { "<Esc>", "<C-[>", "<C-c>", "n" } do
    km.confirm.rules:insert(1, { on = key, run = "app:plugin desktop-dialog -- cancel" })
  end
  for _, key in ipairs { "y", "<Enter>" } do
    km.confirm.rules:insert(1, { on = key, run = "app:plugin desktop-dialog -- submit" })
  end
end

function M:setup()
  if self.initialized then return end
  self.initialized = true
  local req, err = read_request()
  self.dialog = os.getenv("YAZI_PORTAL_FILECHOOSER") == "1" or req ~= nil
  self.confirm_only = req and (req.action == "trash" or req.action == "delete")
  if self.confirm_only then
    self.progress = { active = false, completed = 0, total = #req.targets, percent = 0, action = req.action }
    confirmation_only(self.progress)
  end
  if self.dialog then
    ps.sub("input", function(body)
      if body.type == "cancel" then ya.emit("quit", { no_cwd_file = true }) end
    end)
  end
  if err then ya.async(function() notify(err) end) end
  if not req or req.action == "manage" then return end
  ya.async(function()
    local ok, failure = pcall(function()
      wait_for(req.cwd)
      arm_completion(req)
      ya.exec("reveal", { Url(req.targets[1]) })
      wait_for(req.cwd, req.targets[1])
      select_files(req.targets)
      -- Keep Yazi's native confirmation, task scheduling and trash semantics.
      ya.exec("remove", { permanently = req.action == "delete" })
    end)
    if not ok then notify(failure) end
  end)
end

function M:entry(job)
  if job and job.args[1] == "submit" then
    if self.confirm_only and not self.progress.active then
      self.progress.active = true
      ui.render()
      ya.async(function()
        while not refresh_progress() do ya.sleep(0.1) end
      end)
    end
    ya.emit("confirm:close", { submit = true })
  elseif job and job.args[1] == "cancel" then
    ya.emit("confirm:close", {})
    if self.confirm_only then ya.emit("quit", { no_cwd_file = true }) end
  elseif self.dialog then
    -- Writing a cwd-file would make a canceled folder chooser accept its cwd.
    ya.emit("quit", { no_cwd_file = true })
  else
    ya.emit("escape", {})
  end
end

return M
