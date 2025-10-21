(function () {
  function setModalState(modal, open) {
    if (!modal) return;
    if (open) {
      modal.hidden = false;
      modal.classList.add("is-open");
      modal.querySelector("input, button, textarea, select")?.focus({ preventScroll: true });
    } else {
      modal.classList.remove("is-open");
      modal.hidden = true;
    }
  }

  document.addEventListener("click", function (event) {
    console.log("123");
    const opener = event.target.closest("[data-modal-open]");
    if (opener) {
      event.preventDefault();
      const target = document.querySelector(opener.getAttribute("data-modal-open"));
      setModalState(target, true);
      return;
    }

    const closer = event.target.closest("[data-modal-close]");
    if (closer) {
      const modal = closer.closest(".modal");
      setModalState(modal, false);
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    document.querySelectorAll(".modal.is-open").forEach(function (modal) {
      setModalState(modal, false);
    });
  });
})();
