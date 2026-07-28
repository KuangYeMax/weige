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
    shotType: '中近景',
    aspectRatio: '3:4',
    sceneIndex: -1,
    generating: false,
    result: null,
    showGraded: true,
    models: [],
    selectedProvider: '',
    selectedModel: '',
    comparing: false,
    compareResults: null,
    compareTotalMs: 0,
    favorites: {},

    get scenes() {
      return Array.isArray(this.factCard?.['自然场景']) ? this.factCard['自然场景'] : [];
    },

    get filteredModels() {
      if (!this.selectedProvider) return this.models;
      return this.models.filter(m => m.provider === this.selectedProvider);
    },

    get providers() {
      const set = new Set(this.models.map(m => m.provider));
      return [...set];
    },

    async init() {
      await this.loadHealth();
      await this.loadModels();
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

    async loadModels() {
      try {
        const response = await fetch('/api/models');
        const data = await response.json();
        this.models = data.models || [];
      } catch (_error) {
        this.models = [];
      }
    },

    onProviderChange() {
      this.selectedModel = '';
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
      this.compareResults = null;
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
        this.compareResults = null;
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
        const url = `/api/products/${this.product.product_id}/fact-card`;
        const response = await fetch(url, {
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

    confidenceClass() { return 'medium'; },

    async generate() {
      if (!this.product || this.generating) return;
      this.generating = true;
      this.error = '';
      try {
        const payload = {
          fact_card: this.factCard,
          shot_type: this.shotType,
          scene_index: Math.max(0, this.sceneIndex),
          aspect_ratio: this.aspectRatio,
        };
        if (this.selectedProvider) payload.image_provider = this.selectedProvider;
        if (this.selectedModel) payload.image_model = this.selectedModel;
        const url = `/api/products/${this.product.product_id}/generate`;
        const response = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        this.result = await this.readResponse(response);
      } catch (error) {
        this.error = error.message;
      } finally {
        this.generating = false;
        this.refreshIcons();
      }
    },

    async generateCompare() {
      if (!this.product || this.comparing) return;
      this.comparing = true;
      this.compareResults = null;
      this.error = '';
      try {
        const url = `/api/products/${this.product.product_id}/generate-compare`;
        const response = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            shot_type: this.shotType,
            aspect_ratio: this.aspectRatio,
          }),
        });
        const data = await this.readResponse(response);
        this.compareResults = data.results;
        this.compareTotalMs = data.total_elapsed_ms;
      } catch (error) {
        this.error = error.message;
      } finally {
        this.comparing = false;
        this.refreshIcons();
      }
    },

    toggleFavorite(modelId) {
      this.favorites[modelId] = !this.favorites[modelId];
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
