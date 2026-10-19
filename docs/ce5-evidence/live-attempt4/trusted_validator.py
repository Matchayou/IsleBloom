import ast,os,pathlib,sys
root=pathlib.Path(os.environ['CODE_SOURCE'])
for name in sys.argv[1:]:
    tree=ast.parse((root/(name+'.py')).read_text())
    assert len(tree.body)==1 and isinstance(tree.body[0],ast.Assign)
    statement=tree.body[0]
    assert len(statement.targets)==1 and isinstance(statement.targets[0],ast.Name) and statement.targets[0].id=='ANSWER'
    assert isinstance(statement.value,ast.Constant) and statement.value.value=={'a':7,'b':11}[name]
assert not (root/'.git').exists()
print('Approved AST checks passed for '+','.join(sys.argv[1:])+'; no repository code executed')
