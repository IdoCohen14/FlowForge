const {test} = require('node:test');
const assert = require('node:assert/strict');
const {compareDecimal, selectColumns} = require('../../static/column-tools.js');

test('decimal sorting preserves differences beyond binary-float precision', () => {
  assert.ok(compareDecimal('9007199254740993', '9007199254740992') > 0);
  assert.ok(compareDecimal('1.0000000000000000000000000000001', '1') > 0);
  assert.ok(compareDecimal('-2E+10000', '-1E+10000') < 0);
  assert.ok(compareDecimal('1E-10000', '2E-10000') < 0);
});
test('decimal comparison handles signs, equivalent forms, and zero', () => {
  for (const [a,b] of [['1.00','1e0'],['0','-0.0'],['10e-2','.1'],['-10.0','-1e1']]) assert.equal(compareDecimal(a,b),0);
  assert.ok(compareDecimal('-0.01','0')<0);
  assert.ok(compareDecimal('0','0.01')<0);
  assert.throws(()=>compareDecimal('NaN','1'),TypeError);
});
test('column filtering is literal and case-insensitive, without mutating source', () => {
  const columns=[{name:'Price',missing_values:1},{name:'Price_%',missing_values:0},{name:'Quantity',missing_values:0}];
  assert.deepEqual(selectColumns(columns,' pRiCe ').map(c=>c.name),['Price','Price_%']);
  assert.deepEqual(selectColumns(columns,'%').map(c=>c.name),['Price_%']);
  assert.deepEqual(selectColumns(columns,'absent'),[]);
  selectColumns(columns,'','name_desc');
  assert.deepEqual(columns.map(c=>c.name),['Price','Price_%','Quantity']);
});
test('sort ties remain in file order and nonnumeric columns stay last', () => {
  const columns=[{name:'Text',missing_values:0,numeric:null},
    {name:'B',missing_values:2,numeric:{mean:'9007199254740993'}},
    {name:'A',missing_values:2,numeric:{mean:'9007199254740992'}}];
  assert.deepEqual(selectColumns(columns,'','mean_asc').map(c=>c.name),['A','B','Text']);
  assert.deepEqual(selectColumns(columns,'','mean_desc').map(c=>c.name),['B','A','Text']);
  assert.deepEqual(selectColumns(columns,'','missing_desc').map(c=>c.name),['B','A','Text']);
  assert.deepEqual(selectColumns(columns,'','original'),columns);
});
