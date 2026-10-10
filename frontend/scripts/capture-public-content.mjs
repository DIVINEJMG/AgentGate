import ts from 'typescript';
import { readFileSync, writeFileSync, mkdirSync, readdirSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import path from 'node:path';

// Explicit content fields only. Attributes controlling rendering and authority stay in code.
const properties = new Set(['word','label','title','body','summary','description','signal','name','line','worker','job','detail','tools','output','text','topic','audience','state','price','limit','note','intro','href']);
const attrs = new Set(['alt','aria-label','title','kicker','body','href']);
const root = path.resolve(import.meta.dirname, '..');
const destination = path.join(root, 'content-seed');
mkdirSync(destination, { recursive: true });
mkdirSync(path.join(destination, 'baseline'), { recursive: true });
const inventory = [];
const dictionaries = {};
const transforms = [];
for (const filename of readdirSync(path.join(root, 'src/public')).filter(x => x.endsWith('.tsx'))) {
  const baselinePath = path.join(destination, 'baseline', filename);
  const source = readFileSync(existsSync(baselinePath) ? baselinePath : path.join(root, 'src/public', filename), 'utf8');
  const scope = filename.replace('.tsx','');
  const ast = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const fields = {};
  const changes = [];
  const add = (node, value, category, expression) => {
    if (!value.trim() || /^[\d\s/→↗·.—]+$/.test(value)) return;
    const digest = createHash('sha256').update(`${scope}:${category}:${value}`).digest('hex').slice(0, 10);
    const id = `${category}-${value.toLowerCase().replace(/[^a-z0-9]+/g,'-').slice(0,45).replace(/-$/,'')}-${digest}`;
    fields[id] = value;
    inventory.push({ source: `src/public/${filename}`, line: ast.getLineAndCharacterOfPosition(node.getStart(ast)).line + 1, scope, field: id, category, value });
    changes.push({ start: node.getStart(ast), end: node.end, replacement: expression(id, value) });
  };
  function visit(node) {
    if (ts.isJsxText(node)) {
      // React strips indentation/newlines, but preserves a deliberate single-line space.
      const value = node.text.includes('\n') ? node.text.split(/\r?\n/).map(x => x.trim()).filter(Boolean).join(' ') : node.text;
      add(node, value, 'copy', (id,v) => `{text('${id}', ${JSON.stringify(v)})}`);
      return;
    }
    if (ts.isJsxAttribute(node) && attrs.has(node.name.text) && node.initializer && ts.isStringLiteral(node.initializer)) {
      add(node.initializer, node.initializer.text, node.name.text === 'alt' ? 'alt' : 'copy', (id,v) => `{text('${id}', ${JSON.stringify(v)})}`);
      return;
    }
    if (ts.isStringLiteral(node) && ts.isPropertyAssignment(node.parent) && properties.has(node.parent.name.getText(ast).replace(/['"]/g,''))) {
      add(node, node.text, 'field', (id,v) => `text('${id}', ${JSON.stringify(v)})`);
      return;
    }
    if (ts.isStringLiteral(node)) {
      let boundary = node.parent;
      let blocked = false;
      while (boundary && !ts.isStatement(boundary)) {
        if (ts.isJsxAttribute(boundary) && !attrs.has(boundary.name.text)) { blocked = true; break; }
        boundary = boundary.parent;
      }
      if (blocked) return;
      let ancestor = node.parent;
      while (ancestor && !ts.isStatement(ancestor)) {
        if (ts.isJsxAttribute(ancestor) && !attrs.has(ancestor.name.text)) break;
        if (ts.isPropertyAssignment(ancestor) && ['paragraphs','points','path'].includes(ancestor.name.getText(ast))) {
          add(node,node.text,'field',(id,v)=>`text('${id}', ${JSON.stringify(v)})`); return;
        }
        if (ts.isJsxExpression(ancestor) && (ts.isConditionalExpression(node.parent) && node.parent.condition !== node || scope === 'PublicSite' && ts.isArrayLiteralExpression(node.parent)) && !['home','product','solutions','security','pricing','resources','company','privacy','terms','app','signup','login'].includes(node.text)) {
          add(node,node.text,'copy',(id,v)=>`text('${id}', ${JSON.stringify(v)})`); return;
        }
        ancestor = ancestor.parent;
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  dictionaries[scope] = fields;
  if (!existsSync(baselinePath)) writeFileSync(baselinePath, source);
  transforms.push({ filename, changes });
}
writeFileSync(path.join(destination, 'source-inventory.json'), JSON.stringify(inventory, null, 2)+'\n');
writeFileSync(path.join(destination, 'static-copy.json'), JSON.stringify(dictionaries, null, 2)+'\n');
// Applying transformations is a development operation, never an import/publication step.
if (process.argv.includes('--apply') || process.argv.includes('--preview')) {
  for (const {filename, changes} of transforms) {
    const file = path.join(root, 'src/public', filename);
    let source = readFileSync(path.join(destination,'baseline',filename), 'utf8');
    for (const change of changes.sort((a,b) => b.start-a.start)) source = source.slice(0,change.start)+change.replacement+source.slice(change.end);
    // The module-local text function is bound inside each component below; module collections
    // retain their exact static values until their explicit typed collection adapter is applied.
    source = "import { usePublicText } from './content/ContentContext';\n" + source;
    const declarations = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const movable = declarations.statements.filter(node => ts.isVariableStatement(node) && node.getText(declarations).includes('text('));
    const collectionSource = movable.map(node => node.getText(declarations)).join('\n');
    for (const node of [...movable].sort((a,b)=>b.getStart(declarations)-a.getStart(declarations))) source = source.slice(0,node.getStart(declarations))+source.slice(node.end);
    const ast = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const insertions = [];
    function functions(node) {
      if (ts.isFunctionDeclaration(node) && node.name && node.body && /^[A-Z]/.test(node.name.text)) insertions.push(node.body.getStart(ast)+1);
      ts.forEachChild(node, functions);
    }
    functions(ast);
    const defaultFunction = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.modifiers?.some(m => m.kind === ts.SyntaxKind.DefaultKeyword));
    for (const position of insertions.sort((a,b)=>b-a)) source = source.slice(0,position)+`\n  const text = usePublicText('${filename.replace('.tsx','')}');\n`+(defaultFunction?.body?.getStart(ast)+1 === position ? collectionSource+'\n' : '')+source.slice(position);
    const checked = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    if (checked.parseDiagnostics.length) throw new Error(`Invalid generated syntax: ${filename}`);
    if (process.argv.includes('--preview')) {
      mkdirSync(path.join(destination,'preview'),{recursive:true});
      writeFileSync(path.join(destination,'preview',filename), source);
    } else if (process.argv.includes(`--file=${filename}`)) {
      if (readFileSync(file,'utf8') !== readFileSync(path.join(destination,'baseline',filename),'utf8')) throw new Error(`Refusing to overwrite changed file ${filename}`);
      writeFileSync(file, source);
    }
  }
}
console.log(JSON.stringify({files: transforms.length, fields: inventory.length, scopes: Object.fromEntries(Object.entries(dictionaries).map(([key,value])=>[key,Object.keys(value).length]))}));
