from asyncio import sleep
from inspect import isclass, isgeneratorfunction, iscoroutinefunction
from os.path import join
from pprint import pp
from random import randbytes, randint


class MainClass:
    attrA = 343
    lam = lambda: print("Lambda")

    @staticmethod
    def hello_world():
        print("Hello world")

    @staticmethod
    def sum_int(a, b):
        print(a + b)

    @staticmethod
    def gentest():
        yield 'aaa'

    @staticmethod
    async def asleep(t):
        await sleep(t)

    class SubClass1:
        @staticmethod
        def test_method():
            print("Test method output")

        class Random:
            @staticmethod
            def random_int():
                print(randint(2, 5))

            @staticmethod
            def random_byte():
                print(randbytes(2))


class SecondClass(MainClass):
    def second_method(self):
        pass

    class ThirdClass(MainClass):
        def second_method(self):
            print(self.attrA)


# print(type(MainClass()))

def treegen(cls: type, prefix: str = None):
    classname = cls.__name__
    print("Class:", classname)
    if prefix is None:
        prefix = classname
    else:
        prefix = join(prefix, classname)

    cal = {}
    gen = {}
    awa = {}

    for att in dir(cls):
        if not att.startswith('__'):
            a = cls().__getattribute__(att)
            if callable(a):
                if isclass(a):
                    c, g, a = treegen(a, prefix)
                    cal.update(c)
                    gen.update(g)
                    awa.update(a)
                elif isgeneratorfunction(a):
                    print("Generator:", att)
                    gen[join(prefix, att)] = a
                elif iscoroutinefunction(a):
                    print("Awaitable:", att)
                    awa[join(prefix, att)] = a
                else:
                    print("Callable:", att)
                    cal[join(prefix, att)] = a

    return cal, gen, awa


pp(treegen(MainClass))
