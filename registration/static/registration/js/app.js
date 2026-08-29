(() => {
  const flashMessage = document.querySelector('.messages');
  const form = document.querySelector('#registration-form');
  const quantity = document.querySelector('#id_ticket_quantity');
  const total = document.querySelector('#total-price');
  const basePrice = document.querySelector('#base-price');
  const discountPrice = document.querySelector('#discount-price');
  const whatsapp = document.querySelector('#id_whatsapp_number');
  const shirtSizeFields = document.querySelector('#shirt-size-fields');
  const shirtSizeOptions = ['XS', 'S', 'M', 'L', 'XL', 'XXL', '3XL'];
  const formatter = new Intl.NumberFormat('id-ID');

  const formatRupiah = (amount) => `Rp${formatter.format(amount)}`;

  if (flashMessage) {
    window.setTimeout(() => {
      flashMessage.classList.add('is-hiding');
      window.setTimeout(() => {
        flashMessage.remove();
      }, 300);
    }, 3200);
  }

  if (form && quantity && total && form.dataset.unitPrice) {
    const unitPrice = Number(form.dataset.unitPrice);
    const discountedUnitPrice = Number(form.dataset.discountedUnitPrice || 0);
    const updateTotal = () => {
      const amount = Math.max(1, Number(quantity.value) || 1);
      if (discountedUnitPrice > 0) {
        const discountAmount = Math.max(0, unitPrice - discountedUnitPrice);
        const baseAmount = unitPrice * amount;
        const totalAmount = discountedUnitPrice + Math.max(0, amount - 1) * unitPrice;

        if (basePrice) {
          basePrice.textContent = formatRupiah(baseAmount);
        }

        if (discountPrice) {
          discountPrice.textContent = `-${formatRupiah(discountAmount)}`;
        }

        total.textContent = formatRupiah(totalAmount);
        return;
      }

      total.textContent = formatRupiah(unitPrice * amount);
      if (basePrice) {
        basePrice.textContent = formatRupiah(unitPrice * amount);
      }
      if (discountPrice) {
        discountPrice.textContent = formatRupiah(0);
      }
    };
    quantity.addEventListener('input', updateTotal);
    quantity.addEventListener('change', updateTotal);
    updateTotal();
  }

  if (whatsapp) {
    whatsapp.addEventListener('input', () => {
      whatsapp.value = whatsapp.value.replace(/\D/g, '');
    });
  }

  if (quantity && shirtSizeFields) {
    const renderShirtSizeFields = () => {
      const amount = Math.min(5, Math.max(1, Number(quantity.value) || 1));
      const selectedSizes = Array.from(
        shirtSizeFields.querySelectorAll('select[id^="id_shirt_size_"]'),
        (select) => select.value,
      );
      const fields = [];

      for (let index = 1; index <= amount; index += 1) {
        const selectedSize = selectedSizes[index - 1] || 'M';
        const options = shirtSizeOptions
          .map((size) => `<option${size === selectedSize ? ' selected=""' : ''}>${size}</option>`)
          .join('');

        fields.push(`
          <div class="checkout-field">
            <label for="id_shirt_size_${index}">
              Ukuran Kaos ${index}
            </label>
            <select id="id_shirt_size_${index}" name="shirt_size_${index}">
              ${options}
            </select>
          </div>
        `);
      }

      shirtSizeFields.innerHTML = fields.join('');
    };

    quantity.addEventListener('input', renderShirtSizeFields);
    quantity.addEventListener('change', renderShirtSizeFields);
    renderShirtSizeFields();
  }
})();
