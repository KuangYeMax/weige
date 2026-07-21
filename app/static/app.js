function productStudio() {
  return {
    health: null,
    error: '',
    dragging: false,
    selectedFile: null,
    previewUrl: '',
    uploading: false,
    product: null,
    visionProvider: '—',
    factCard: {},
    factJson: '',
    factSaved: false,
    savingFact: false,
    shotType: '完整照',
    aspectRatio: '3:4',
    sceneIndex: -1,
    generating: false,
    result: null,

    get scenes() {
      return Array.isArray(this.factCard?.['自然场景']) ? this.factCard['自然场景'] : [];
    },

    async init() {
      await this.loadHealth();
      this.refreshIcons();
    },

    async loadHealth() {
      try {
        const response = await fetch('/api/health');
        this.health = await response.json();
      } catch (_error) {
        this.error = '无法连接本地服务，请确认后端已经启动。';
      }
    },

    handleDrop(event) {
      this.dragging = false;
      this.handleFile(event.dataTransfer.files?.[0]);
    },

    handleFile(file) {
      if (!file) return;
      const allowed = ['image/jpeg', 'image/png', 'image/webp'];
      if (!allowed.includes(file.type)) {
        this.error = '请选择 JPG、JPEG、PNG 或 WEBP 图片。';
        return;
      }
      if (file.size > 10 * 1024 * 1024) {
        this.error = '图片不能超过 10MB。';
        return;
      }
      if (this.previewUrl) URL.revokeObjectURL(this.previewUrl);
      this.selectedFile = file;
      this.previewUrl = URL.createObjectURL(file);
      this.product = null;
      this.result = null;
      this.factSaved = false;
      this.error = '';
      this.refreshIcons();
    },

    async upload() {
      if (!this.selectedFile || this.uploading) return;
      this.uploading = true;
      this.error = '';
      const body = new FormData();
      body.append('image', this.selectedFile);
      try {
        const response = await fetch('/api/products/upload', { method: 'POST', body });
        const payload = await this.readResponse(response);
        this.product = payload;
        this.visionProvider = payload.vision_provider;
        this.factCard = payload.fact_card;
        this.factJson = JSON.stringify(this.factCard, null, 2);
        this.factSaved = true;
        this.selectSafeDefaultScene();
        this.result = null;
      } catch (error) {
        this.error = error.message;
      } finally {
        this.uploading = false;
        this.refreshIcons();
      }
    },

    markFactChanged() {
      this.factJson = JSON.stringify(this.factCard, null, 2);
      this.factSaved = false;
    },

    parseFactJson() {
      let parsed;
      try {
        parsed = JSON.parse(this.factJson);
      } catch (_error) {
        throw new Error('事实卡 JSON 格式有误，请检查逗号、引号和括号。');
      }
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        throw new Error('事实卡必须是一个 JSON 对象。');
      }
      return parsed;
    },

    async saveFactCard() {
      if (!this.product || this.savingFact) return;
      this.savingFact = true;
      this.error = '';
      try {
        const parsed = this.parseFactJson();
        const response = await fetch(`/api/products/${this.product.product_id}/fact-card`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(parsed),
        });
        const payload = await this.readResponse(response);
        this.factCard = payload.fact_card;
        this.factJson = JSON.stringify(this.factCard, null, 2);
        this.factSaved = true;
        this.selectSafeDefaultScene();
      } catch (error) {
        this.error = error.message;
        this.factSaved = false;
      } finally {
        this.savingFact = false;
        this.refreshIcons();
      }
    },

    async copyJson() {
      try {
        await navigator.clipboard.writeText(this.factJson);
      } catch (_error) {
        this.error = '浏览器未允许复制，请在 JSON 编辑器中手动选择。';
      }
    },

    selectSafeDefaultScene() {
      this.sceneIndex = this.scenes.length > 0 ? 0 : -1;
    },

    confidenceClass(scene) {
      return 'medium';
    },

    async generate() {
      if (!this.product || this.sceneIndex < 0 || this.generating) return;
      this.generating = true;
      this.error = '';
      try {
        const response = await fetch(`/api/products/${this.product.product_id}/generate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            fact_card: this.factCard,
            shot_type: this.shotType,
            scene_index: this.sceneIndex,
            aspect_ratio: this.aspectRatio,
          }),
        });
        this.result = await this.readResponse(response);
      } catch (error) {
        this.error = error.message;
      } finally {
        this.generating = false;
        this.refreshIcons();
      }
    },

    async readResponse(response) {
      let payload;
      try {
        payload = await response.json();
      } catch (_error) {
        throw new Error('服务返回了无法读取的响应。');
      }
      if (!response.ok) {
        throw new Error(payload?.error?.message || '请求失败，请稍后重试。');
      }
      return payload;
    },

    formatBytes(bytes) {
      if (!bytes) return '0 B';
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
      return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
    },

    formatTime(value) {
      if (!value) return '—';
      return new Intl.DateTimeFormat('zh-CN', {
        dateStyle: 'medium', timeStyle: 'short',
      }).format(new Date(value));
    },

    refreshIcons() {
      this.$nextTick(() => window.lucide?.createIcons());
    },
  };
}
