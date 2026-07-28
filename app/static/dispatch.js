function dispatchApp() {
  return {
    codeMap: {},
    codeInput: '',
    batchInput: '',
    form: { wx_remark: '', send_codes: [], trigger_at: '' },
    submitting: false,
    formError: '',
    formSuccess: '',
    tasks: [],
    tasksLoading: true,
    settings: null,
    filter: new URLSearchParams(window.location.search).get('filter') || '',
    verifying: false,
    verifyResult: 'untested',
    verifyHeaderName: '',
    verifyFailReason: '',

    get canSubmit() {
      if (this.verifyResult === 'fail') return false;
      return this.form.wx_remark.trim() &&
             this.form.send_codes.length > 0 &&
             this.form.send_codes.every(s => s.valid);
    },

    get triggerPreview() {
      if (!this.form.trigger_at) return '—';
      try {
        const d = new Date(this.form.trigger_at);
        return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
      } catch { return this.form.trigger_at; }
    },

    get filteredTasks() {
      let list = [...this.tasks];
      if (this.filter === 'needs_action') {
        list = list.filter(t => t.status === 'needs_review' || t.status === 'awaiting_confirmation');
      } else if (this.filter === 'in_progress') {
        list = list.filter(t => ['generating', 'ready', 'sending'].includes(t.status));
      } else if (this.filter === 'pending') {
        list = list.filter(t => t.status === 'pending');
      } else if (this.filter === 'done') {
        list = list.filter(t => ['sent', 'abandoned'].includes(t.status));
      } else if (this.filter === 'due_today') {
        const today = new Date().toDateString();
        list = list.filter(t => t.status === 'pending' && new Date(t.trigger_at).toDateString() === today);
      } else if (this.filter === 'failed_today') {
        list = list.filter(t => t.status === 'failed');
      }
      const priority = { needs_review: 0, awaiting_confirmation: 1, generating: 2, ready: 3, sending: 4, pending: 5, failed: 6, sent: 7, abandoned: 8 };
      list.sort((a, b) => (priority[a.status] ?? 99) - (priority[b.status] ?? 99));
      return list;
    },

    async init() {
      await this.loadCodeMap();
      await this.loadTasks();
      await this.loadSettings();
      this._initTriggerAt();
      setInterval(() => this.loadTasks(), 4000);
      setInterval(() => this.loadSettings(), 10000);
    },

    _initTriggerAt() {
      this.form.trigger_at = this._defaultTriggerAt();
    },

    _defaultTriggerAt() {
      const d = new Date();
      d.setDate(d.getDate() + 3);
      return this._toDatetimeLocal(d);
    },

    _toDatetimeLocal(d) {
      const pad = n => String(n).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
    },

    async loadSettings() {
      try { this.settings = await (await fetch('/api/settings')).json(); } catch {}
    },

    async loadCodeMap() {
      try {
        const data = await (await fetch('/api/products')).json();
        const map = {};
        for (const p of (data.products || [])) {
          for (const c of p.codes) { map[c.code] = { name: p.name, image_path: p.image_path }; }
        }
        this.codeMap = map;
      } catch {}
    },

    async verifyRemark() {
      this.verifying = true;
      this.verifyResult = 'untested';
      try {
        const res = await fetch('/api/dispatch/verify-remark', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ wx_remark: this.form.wx_remark.trim() }),
        });
        if (res.ok) {
          const d = await res.json();
          this.verifyResult = 'pass';
          this.verifyHeaderName = d.header_name || '';
        } else {
          const d = await res.json();
          this.verifyResult = 'fail';
          this.verifyFailReason = d.error?.message || '校验失败';
        }
      } catch {
        this.verifyResult = 'fail';
        this.verifyFailReason = '网络错误';
      } finally { this.verifying = false; }
    },

    addSendCode() {
      const code = this.codeInput.trim();
      if (!code || this.form.send_codes.length >= 4) return;
      if (this.form.send_codes.some(s => s.code === code)) { this.formError = '编号已添加'; return; }
      this.formError = '';
      const info = this.codeMap[code];
      this.form.send_codes.push({ code, valid: !!info, product_name: info ? info.name : '', image_path: info ? info.image_path : '' });
      this.codeInput = '';
    },

    removeSendCode(idx) { this.form.send_codes.splice(idx, 1); },

    applyBatchInput() {
      const text = this.batchInput.trim();
      if (!text) return;
      const lines = text.split('\n').filter(l => l.trim());
      if (!lines.length) return;
      const first = lines[0].trim();
      const spaceIdx = first.indexOf(' ');
      if (spaceIdx === -1) { this.form.wx_remark = first; this.batchInput = ''; return; }
      this.form.wx_remark = first.substring(0, spaceIdx).trim();
      const rawCodes = first.substring(spaceIdx + 1).trim();
      const codes = rawCodes.split(/[,，、\s]+/).filter(c => c.trim());
      this.form.send_codes = [];
      for (const code of codes) {
        if (this.form.send_codes.length >= 4) break;
        if (this.form.send_codes.some(s => s.code === code)) continue;
        const info = this.codeMap[code];
        this.form.send_codes.push({ code, valid: !!info, product_name: info ? info.name : '', image_path: info ? info.image_path : '' });
      }
      for (let i = 1; i < lines.length; i++) {
        if (this.form.send_codes.length >= 4) break;
        const tokens = lines[i].trim().split(/[,，、\s]+/).filter(c => c.trim());
        for (const token of tokens) {
          if (this.form.send_codes.length >= 4) break;
          if (this.form.send_codes.some(s => s.code === token)) continue;
          const info = this.codeMap[token];
          this.form.send_codes.push({ code: token, valid: !!info, product_name: info ? info.name : '', image_path: info ? info.image_path : '' });
        }
      }
      this.verifyResult = 'untested';
      this.batchInput = '';
    },

    async submit() {
      this.formError = ''; this.formSuccess = '';
      if (!this.canSubmit) return;
      this.submitting = true;
      try {
        const payload = {
          wx_remark: this.form.wx_remark.trim(),
          send_codes: this.form.send_codes.map(s => s.code),
          trigger_at: new Date(this.form.trigger_at).toISOString(),
        };
        const res = await fetch('/api/dispatch', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload) });
        if (!res.ok) { const e = await res.json(); this.formError = e.error?.message || '提交失败'; return; }
        this.formSuccess = '登记成功';
        this.form = { wx_remark: '', send_codes: [], trigger_at: this._defaultTriggerAt() };
        this.verifyResult = 'untested';
        await this.loadTasks();
      } catch { this.formError = '网络错误'; }
      finally { this.submitting = false; }
    },

    async loadTasks() {
      try {
        const raw = await (await fetch('/api/dispatch')).json();
        this.tasks = raw.map(t => ({
          ...t,
          _expanded: (this.tasks.find(x => x.task_id === t.task_id) || {})._expanded || false,
          _manifest: (this.tasks.find(x => x.task_id === t.task_id) || {})._manifest || null,
          _manifestLoading: false,
        }));
      }
      catch {} finally { this.tasksLoading = false; }
    },

    async toggleExpand(task) {
      task._expanded = !task._expanded;
      if (task._expanded && !task._manifest) {
        await this.loadManifest(task);
      }
    },

    async loadManifest(task) {
      task._manifestLoading = true;
      try {
        const res = await fetch(`/api/dispatch/${task.task_id}`);
        if (res.ok) {
          const data = await res.json();
          if (data.manifest && data.manifest.results) {
            task._manifest = {
              ...data.manifest,
              results: data.manifest.results.map(r => ({ ...r, _regenerating: false })),
            };
          } else {
            task._manifest = { results: [] };
          }
        }
      } catch {} finally { task._manifestLoading = false; }
    },

    async deleteTask(task) {
      if (!confirm(`确定删除 ${task.wx_remark} 的待发记录？`)) return;
      try {
        const res = await fetch(`/api/dispatch/${task.task_id}`, { method: 'DELETE' });
        if (res.ok) {
          await this.loadTasks();
        } else {
          const d = await res.json();
          alert((d.error && d.error.message) || '删除失败');
        }
      } catch { alert('网络错误'); }
    },

    async regenerateItem(task, code, type) {
      const item = task._manifest.results.find(r => r.code === code);
      if (!item) return;
      item._regenerating = true;
      try {
        const res = await fetch(`/api/dispatch/${task.task_id}/regenerate/${encodeURIComponent(code)}`, { method: 'POST' });
        if (res.ok) {
          const data = await res.json();
          item.image_url = data.image_url;
          item.content_text = data.content_text;
        } else {
          const d = await res.json();
          alert((d.error && d.error.message) || '重新生成失败');
        }
      } catch { alert('网络错误'); }
      finally { item._regenerating = false; }
    },

    formatTime(iso) { return window._formatTime(iso); },
    badgeClass(s) { return window._badgeClass(s); },
  };
}
