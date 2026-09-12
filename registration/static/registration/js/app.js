(() => {
  const header = document.querySelector('.header');
  const headerContainer = document.querySelector('.header-container');
  const navbar = document.querySelector('.navbar');
  const auth = document.querySelector('.header-container .auth');

  if (header && headerContainer && navbar && auth) {
    const menuToggle = document.createElement('button');
    const menuOverlay = document.createElement('button');
    const mobileMenuQuery = window.matchMedia('(max-width: 768px)');

    menuToggle.className = 'menu-toggle';
    menuToggle.type = 'button';
    menuToggle.setAttribute('aria-controls', 'mobile-navigation');
    menuToggle.setAttribute('aria-expanded', 'false');
    menuToggle.setAttribute('aria-label', 'Buka menu navigasi');
    menuToggle.innerHTML = '<span></span><span></span><span></span>';

    navbar.id = 'mobile-navigation';
    menuOverlay.className = 'menu-overlay';
    menuOverlay.type = 'button';
    menuOverlay.setAttribute('aria-label', 'Tutup menu navigasi');
    menuOverlay.hidden = true;

    const closeMenu = () => {
      header.classList.remove('menu-open');
      document.body.classList.remove('menu-open');
      menuToggle.setAttribute('aria-expanded', 'false');
      menuToggle.setAttribute('aria-label', 'Buka menu navigasi');
      menuOverlay.hidden = true;
    };

    const syncMenuLayout = () => {
      if (mobileMenuQuery.matches) {
        navbar.insertBefore(auth, navbar.firstChild);
      } else {
        headerContainer.appendChild(auth);
      }
      closeMenu();
    };

    const openMenu = () => {
      header.classList.add('menu-open');
      document.body.classList.add('menu-open');
      menuToggle.setAttribute('aria-expanded', 'true');
      menuToggle.setAttribute('aria-label', 'Tutup menu navigasi');
      menuOverlay.hidden = false;
    };

    menuToggle.addEventListener('click', () => {
      if (header.classList.contains('menu-open')) {
        closeMenu();
      } else {
        openMenu();
      }
    });
    menuOverlay.addEventListener('click', closeMenu);
    navbar.addEventListener('click', (event) => {
      if (event.target.closest('a')) {
        closeMenu();
      }
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        closeMenu();
      }
    });
    window.addEventListener('scroll', () => {
      if (header.classList.contains('menu-open')) {
        closeMenu();
      }
    }, { passive: true });
    mobileMenuQuery.addEventListener('change', syncMenuLayout);

    syncMenuLayout();
    headerContainer.insertBefore(menuToggle, navbar);
    document.body.appendChild(menuOverlay);
  }

  const flashMessage = document.querySelector('.messages');
  const form = document.querySelector('#registration-form');
  const quantity = document.querySelector('#id_ticket_quantity');
  const total = document.querySelector('#total-price');
  const basePrice = document.querySelector('#base-price');
  const discountPrice = document.querySelector('#discount-price');
  const whatsapp = document.querySelector('#id_whatsapp_number');
  const shirtSizeFields = document.querySelector('#shirt-size-fields');
  const shirtSizeOptions = ['XS', 'S', 'M', 'L', 'XL', 'XXL', '3XL', '4XL', '5XL', '6XL'];
  const formatter = new Intl.NumberFormat('id-ID');
  const actionModal = document.querySelector('#actionModal');
  const modalTransactionId = document.querySelector('#modal_tx_id');
  const modalSizeFields = document.querySelector('#modal_size_fields');
  const modalSizeSection = document.querySelector('#modal_size_section');
  const modalUnavailable = document.querySelector('#modal_size_unavailable');
  const modalUpdateButton = document.querySelector('#modal_update_button');
  const modalCloseButton = document.querySelector('#closeActionModal');

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

  if (actionModal && modalTransactionId && modalSizeFields && modalUpdateButton) {
    const renderModalSizeFields = (count, selectedSizes) => {
      const fields = [];

      for (let index = 1; index <= count; index += 1) {
        const selectedSize = selectedSizes[index - 1] || 'M';
        const options = shirtSizeOptions
          .map((size) => `<option value="${size}"${size === selectedSize ? ' selected=""' : ''}>${size}</option>`)
          .join('');

        fields.push(`
          <div class="checkout-field">
            <label for="id_modal_shirt_size_${index}">
              Ukuran Kaos ${index}
            </label>
            <select id="id_modal_shirt_size_${index}" name="new_sizes">
              ${options}
            </select>
          </div>
        `);
      }

      modalSizeFields.innerHTML = fields.join('');
    };

    const closeModal = () => {
      actionModal.style.display = 'none';
      modalTransactionId.value = '';
      modalSizeFields.innerHTML = '';
    };

    const openModal = (button) => {
      const transactionId = button.dataset.transactionId || '';
      const ticketQuantity = Math.max(1, Number(button.dataset.ticketQuantity) || 1);
      const hasTshirtSizes = button.dataset.hasTshirtSizes === 'true';
      const selectedSizes = (button.dataset.shirtSizes || '')
        .split(',')
        .map((size) => size.trim())
        .filter(Boolean);

      modalTransactionId.value = transactionId;
      actionModal.style.display = 'flex';

      if (hasTshirtSizes) {
        modalSizeSection.style.display = 'block';
        modalUnavailable.style.display = 'none';
        modalUpdateButton.disabled = false;
        modalUpdateButton.style.opacity = '1';
        modalUpdateButton.style.cursor = 'pointer';
        renderModalSizeFields(ticketQuantity, selectedSizes);
      } else {
        modalSizeSection.style.display = 'none';
        modalUnavailable.style.display = 'block';
        modalUpdateButton.disabled = true;
        modalUpdateButton.style.opacity = '0.6';
        modalUpdateButton.style.cursor = 'not-allowed';
        modalSizeFields.innerHTML = '';
      }
    };

    window.openTicketModal = openModal;
    window.closeTicketModal = closeModal;

    document.querySelectorAll('[data-open-ticket-modal="true"]').forEach((button) => {
      button.addEventListener('click', () => openModal(button));
    });

    if (modalCloseButton) {
      modalCloseButton.addEventListener('click', closeModal);
    }

    actionModal.addEventListener('click', (event) => {
      if (event.target === actionModal) {
        closeModal();
      }
    });
  }

  if (form) {
    const termsCheckbox = form.querySelector('input[name="accept_terms"]');
    const termsSubmit = form.querySelector('[data-terms-submit="true"]');
    const termsOpeners = form.querySelectorAll('[data-open-terms="true"]');

    if (termsCheckbox && termsSubmit && termsOpeners.length) {
      const modal = document.createElement('div');
      modal.className = 'terms-modal';
      modal.setAttribute('aria-hidden', 'true');
      modal.innerHTML = `
        <section class="terms-modal__dialog" aria-labelledby="terms-modal-title" aria-modal="true" role="dialog">
          <header class="terms-modal__header">
            <h2 id="terms-modal-title">Syarat dan Ketentuan Peserta</h2>
            <button class="terms-modal__close" type="button" aria-label="Tutup">&times;</button>
          </header>
          <div class="terms-modal__body" tabindex="0">
            <h3>Peserta dan Perlengkapan</h3>
            <ul>
              <li>Peserta merupakan Civitas Akademika, yaitu mahasiswa, alumni, dosen, tenaga kependidikan, dan keluarga. Tidak ada syarat umur peserta.</li>
              <li>Peserta wajib mendaftar resmi melalui dn.cs.ui.ac.id dan mengisi formulir dengan data yang benar, akurat, dan lengkap.</li>
              <li>Data peserta, seperti nama, jenis kelamin, ukuran jersey, dan lain-lain tidak dapat diubah atau diganti setelah pembayaran formulir registrasi.</li>
              <li>Panitia tidak bertanggung jawab atas segala kerugian yang ditimbulkan akibat kesalahan pengisian data peserta dan tidak melayani penggantian ukuran jersey pada saat pembagian fun kit.</li>
              <li>Tiket tidak dapat dipindahtangankan dan tidak dapat direfund, baik sebagian maupun seluruhnya.</li>
              <li>Pengambilan fun kit wajib membawa e-ticket serta kartu identitas yang sesuai. Peserta wajib memakai gelang partisipasi pada hari acara.</li>
              <li>Barang pribadi merupakan tanggung jawab peserta; kehilangan atau pencurian bukan tanggung jawab panitia.</li>
            </ul>
            <h3>Kesehatan dan Keselamatan</h3>
            <ul>
              <li>Peserta wajib dalam kondisi sehat jasmani dan rohani. Keselamatan selama kegiatan menjadi tanggung jawab masing-masing peserta.</li>
              <li>Panitia tidak bertanggung jawab atas kerugian akibat risiko kesehatan pribadi, penyakit bawaan, keterlambatan hadir, dan kelalaian peserta.</li>
              <li>Panitia menyediakan tenaga medis, dokter, petugas pertolongan pertama, ambulans, dan obat-obatan.</li>
              <li>Pada cedera, panitia hanya memberikan pertolongan pertama atau P3K dan mengantar ke rumah sakit rujukan terdekat. Seluruh biaya pengobatan serta perawatan ditanggung peserta.</li>
              <li>Peserta yang tidak mampu melanjutkan kegiatan wajib menepi dan menghubungi panitia atau petugas medis.</li>
            </ul>
            <h3>Aturan Pelaksanaan</h3>
            <ul>
              <li>Titik start dan finish berada di Gedung Baru Fasilkom UI.</li>
              <li>Peserta hadir paling lambat pukul 05.00 WIB, mengikuti rute resmi, arahan panitia, menjaga ketertiban, fasilitas acara, dan kebersihan.</li>
              <li>Dilarang membawa kendaraan, sepeda, alat beroda, hewan peliharaan, kostum atau perlengkapan berisiko membahayakan, penutup wajah penuh, atau perangkat dengan volume mengganggu.</li>
              <li>Dilarang melakukan aktivitas yang menghambat kelancaran event di area start, finish, maupun rute.</li>
              <li>Peserta wajib berperilaku sopan, mematuhi peraturan serta arahan panitia, menjaga ketertiban dan fasilitas acara, menghormati semua pihak, serta membuang sampah pada tempat yang disediakan.</li>
            </ul>
            <h3>Fasilitas Acara dan Keselamatan</h3>
            <ul>
              <li>Panitia menyediakan pos medis, refreshment/water station, dan petugas keamanan pada titik yang ditentukan.</li>
              <li>Panitia atau tenaga medis berhak menghentikan peserta yang dinilai secara medis tidak dapat melanjutkan kegiatan.</li>
              <li>Panitia berhak menolak pengambilan fun kit atau layanan lainnya bila peserta tidak memenuhi ketentuan atau berada di luar waktu yang ditentukan.</li>
            </ul>
            <h3>Dokumentasi dan Pembatalan</h3>
            <ul>
              <li>Panitia berhak menggunakan foto atau video kegiatan tanpa batas waktu. Peserta dilarang membawa fotografer pribadi.</li>
              <li>Panitia berhak membatalkan acara apabila terjadi keadaan di luar kendali atau force majeure, termasuk bencana alam dan demonstrasi.</li>
            </ul>
          </div>
          <footer class="terms-modal__footer">
            <p class="terms-modal__status">Gulir sampai akhir untuk mengaktifkan persetujuan.</p>
          </footer>
        </section>
      `;
      document.body.appendChild(modal);

      const modalBody = modal.querySelector('.terms-modal__body');
      const modalStatus = modal.querySelector('.terms-modal__status');
      const closeModal = () => {
        modal.classList.remove('is-open');
        modal.setAttribute('aria-hidden', 'true');
      };
      const unlockTerms = () => {
        termsCheckbox.disabled = false;
        modalStatus.textContent = 'Syarat dan ketentuan telah dibaca. Anda dapat mencentang persetujuan.';
        modalStatus.classList.add('is-ready');
      };
      const checkScrollProgress = () => {
        if (modalBody.scrollTop + modalBody.clientHeight >= modalBody.scrollHeight - 8) {
          unlockTerms();
        }
      };

      termsSubmit.disabled = true;
      termsCheckbox.addEventListener('change', () => {
        termsSubmit.disabled = !termsCheckbox.checked;
      });
      termsOpeners.forEach((opener) => {
        opener.addEventListener('click', () => {
          modal.classList.add('is-open');
          modal.setAttribute('aria-hidden', 'false');
          modalBody.focus();
          checkScrollProgress();
        });
      });
      modalBody.addEventListener('scroll', checkScrollProgress);
      modal.querySelector('.terms-modal__close').addEventListener('click', closeModal);
      modal.addEventListener('click', (event) => {
        if (event.target === modal) {
          closeModal();
        }
      });
    }
  }
})();
