document.addEventListener('alpine:init', () => {
  Alpine.data('productList', () => ({
    products: [],
    loading: true,
    uploading: false,

    async init() {
      await this.fetchProducts();
    },

    async fetchProducts() {
      this.loading = true;
      try {
        const res = await fetch('/api/products');
        const data = await res.json();
        this.products = (data.products || []).map(p => ({
          ...p,
          _newCode: '',
          _error: '',
          _submitting: false,
        }));
      } catch {
        this.products = [];
      } finally {
        this.loading = false;
      }
    },

    async uploadProduct(event) {
      const file = event.target.files[0];
      if (!file) return;
      this.uploading = true;
      try {
        const formData = new FormData();
        formData.append('image', file);
        const res = await fetch('/api/products/upload', { method: 'POST', body: formData });
        if (res.ok) {
          await this.fetchProducts();
        } else {
          const d = await res.json();
          alert((d.error && d.error.message) || '上传失败');
        }
      } catch {
        alert('网络错误，上传失败');
      } finally {
        this.uploading = false;
        event.target.value = '';
      }
    },

    async addCode(product) {
      product._error = '';
      const code = (product._newCode || '').trim();
      if (!code) {
        product._error = '编号不能为空';
        return;
      }
      product._submitting = true;
      try {
        const res = await fetch(`/api/products/${product.product_id}/codes`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code }),
        });
        const data = await res.json();
        if (!res.ok) {
          product._error = (data.error && data.error.message) || '新增失败';
          return;
        }
        product.codes.push({ code: data.code, created_at: data.created_at });
        product._newCode = '';
      } catch {
        product._error = '网络错误，请重试';
      } finally {
        product._submitting = false;
      }
    },

    async deleteProduct(product) {
      if (!confirm(`确定要删除产品「${product.name || '未命名'}」吗？\n此操作会同时删除所有相关文件，不可恢复！`)) return;
      try {
        const res = await fetch(`/api/products/${product.product_id}`, { method: 'DELETE' });
        if (res.ok) {
          this.products = this.products.filter(p => p.product_id !== product.product_id);
        } else {
          const d = await res.json();
          alert((d.error && d.error.message) || '删除失败');
        }
      } catch {
        alert('网络错误，删除失败');
      }
    },
  }));
});
