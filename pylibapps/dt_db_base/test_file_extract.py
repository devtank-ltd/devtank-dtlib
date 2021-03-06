import os
import sys

if sys.version_info[0] < 3:

    import parser


    def _find_arg_in_st(st):
        if type(st) is str:
            if st not in '[].(),' and st != 'args' and st != 'get' and \
               st != 'None':
                return st
        elif type(st) is list:
            for st2 in st:
                r = _find_arg_in_st(st2)
                if r != None:
                    return r
        return None


    def _get_args_in_st(st, args):
        if type(st) is str:
            if st == "args":
                return 1
        elif type(st) is list:
            is_args = 0
            for st2 in st:
                is_args |= _get_args_in_st(st2, args)
            if is_args:
                if is_args == 3:
                    valid_args = _find_arg_in_st(st)
                    if valid_args:
                        args[eval(valid_args)] = True
                else:
                    return is_args + 1
        return 0


    def get_args_in_src(test_file):
        args = {'exit_on_fail': True}
        with open(test_file,"r") as f:
            s = f.read()
            try:
                st = parser.suite(s)
                _get_args_in_st(st.tolist(), args)
                return args
            except Exception as e:
                raise Exception('Failed to parse file "%s":\n\t%s' % (os.path.basename(test_file), str(e)))
else:
    from tokenize import tokenize, NAME, OP

    def get_args_in_src(test_file):
        args = {'exit_on_fail': True}
        with open(test_file,"rb") as f:
            try:
                tokens = list(tokenize(f.readline))
                for n in range(0, len(tokens)):
                    token = tokens[n]
                    if token[0] == NAME and token[1] == "args" and n < (len(tokens)-2):
                        op_token = tokens[n+1]
                        arg_token = tokens[n+2]
                        if op_token[0] == OP and op_token[1] == "[" \
                          and arg_token[0] == NAME:
                            args[arg_token[1]] = True
            except Exception as e:
                raise Exception('Failed to parse file "%s":\n\t%s' % (os.path.basename(test_file), str(e)))
        return args


def get_test_doc(test_file):
    with open(test_file,"r") as f:
        lines = f.readlines()
        header_comment_open = False
        doc_lines = []
        for line in lines:
            if line.startswith('"""') or line.startswith("'''"):
                line = line.replace("'''","")
                line = line.replace('"""','')
                if not header_comment_open:
                    header_comment_open = True
                else:
                    break
            if header_comment_open and (len(doc_lines) or len(line) > 1):
                doc_lines += [line]
        return "".join(doc_lines)
