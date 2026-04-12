/**
 * Boots react-scripts with the compatibility polyfill preloaded.
 */
require('../node-prefix-polyfill');

const script = process.argv[2];

if (!script) {
  console.error('Missing react-scripts target (start/build/test).');
  process.exit(1);
}

// Delegate to the requested react-scripts command.
// eslint-disable-next-line @typescript-eslint/no-var-requires
require(`react-scripts/scripts/${script}`);
