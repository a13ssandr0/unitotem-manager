<template>
  <div class="card-column">
    <slot></slot>
    <!-- The floating action button: always at the bottom of the visible page and at the right edge of the card -->
    <div v-if="$slots.fab" class="fab-row">
      <div class="fab-holder"><slot name="fab"></slot></div>
    </div>
  </div>
</template>

<script setup>
/**
 * Page layout for a column of cards, optionally with a floating action button. The button sits at the bottom of the
 * visible area and its right edge is the right edge of the card (what Android's ConstraintLayout calls
 * endToEnd of the card), whatever the window width: the cards are 1000 px wide with a 16 px margin, and the button row
 * has exactly that width, so the two always line up. When the page is short the button is at the bottom of the
 * screen, when it scrolls the button stays on screen (sticky).
 */
</script>

<style scoped>
.card-column {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-height: 100%;
}
.fab-row {
  position: sticky;
  bottom: calc(16px + var(--taskbar-height, 0px));
  width: 100%;
  max-width: 1032px;
  box-sizing: border-box;
  padding-right: 16px;
  margin-top: auto;
  display: flex;
  justify-content: flex-end;
  pointer-events: none;
  z-index: 5;
}
.fab-holder {
  pointer-events: auto;
}
</style>
