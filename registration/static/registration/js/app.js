(() => {
  const form = document.querySelector('#registration-form');
  const quantity = document.querySelector('#id_ticket_quantity');
  const total = document.querySelector('#total-price');

  if (form && quantity && total && form.dataset.unitPrice) {
    const unitPrice = Number(form.dataset.unitPrice);
    const formatter = new Intl.NumberFormat('id-ID');
    const updateTotal = () => {
      const amount = Math.max(1, Number(quantity.value) || 1);
      total.textContent = `Rp${formatter.format(unitPrice * amount)}`;
    };
    quantity.addEventListener('input', updateTotal);
    updateTotal();
  }
})();