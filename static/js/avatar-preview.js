(() => {
  function updatePreview(preview, file) {
    if (!preview) return;
    if (file) {
      const reader = new FileReader();
      reader.onload = e => { preview.src = e.target?.result || preview.src; };
      reader.readAsDataURL(file);
    } else if (preview.dataset.defaultSrc) {
      preview.src = preview.dataset.defaultSrc;
    }
  }

  document.addEventListener("change", (event) => {
    const input = event.target;
    if (!(input instanceof HTMLInputElement)) return;
    if (!input.matches("[data-avatar-input]")) return;
    const wrapper = input.closest("[data-avatar-field]") || document;
    const preview = wrapper.querySelector("[data-avatar-preview]");
    const file = input.files && input.files[0];
    updatePreview(preview, file);
  });
})();
