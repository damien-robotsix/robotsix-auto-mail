module.exports = {
  rules: {
    // Color
    "color-no-invalid-hex": true,

    // Font family
    "font-family-no-duplicate-names": true,
    "font-family-no-missing-generic-family-keyword": true,

    // Function
    "function-calc-no-unspaced-operator": true,
    "function-linear-gradient-no-nonstandard-direction": true,

    // String
    "string-no-newline": true,

    // Unit
    "unit-no-unknown": true,

    // Property
    "property-no-unknown": true,

    // Keyframe declaration
    "keyframe-declaration-no-important": true,

    // Declaration block
    "declaration-block-no-duplicate-custom-properties": true,
    "declaration-block-no-duplicate-properties": [
      true,
      { ignore: ["consecutive-duplicates-with-different-values"] },
    ],
    "declaration-block-no-shorthand-property-overrides": true,

    // Block
    "block-no-empty": true,

    // Selector
    "selector-pseudo-class-no-unknown": true,
    "selector-pseudo-element-no-unknown": true,
    "selector-type-no-unknown": true,

    // Media feature
    "media-feature-name-no-unknown": true,

    // At-rule
    "at-rule-no-unknown": [
      true,
      {
        ignoreAtRules: [
          "apply",
          "layer",
          "screen",
          "tailwind",
          "variants",
          "responsive",
        ],
      },
    ],

    // Comment
    "comment-no-empty": true,

    // General / Sheet
    "no-descending-specificity": true,
    "no-duplicate-at-import-rules": true,
    "no-duplicate-selectors": true,
    "no-empty-source": true,
    "no-invalid-double-slash-comments": true,
    "no-invalid-position-at-import-rule": [
      true,
      { ignoreAtRules: ["use"] },
    ],
    "no-irregular-whitespace": true,
  },
};
