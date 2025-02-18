from loguru import logger

class MainClass:
    def __init__(self):
        logger.debug("MainClass.__init__")
        logger.debug(self)

    subclasses = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.subclasses.append(cls)


class MySubclass(MainClass):
    pass

class MySubclass2(MySubclass):
    pass

logger.debug(MySubclass.subclasses)
logger.debug(MySubclass.__subclasses__())
logger.debug(MySubclass2.subclasses)


class Person:
    def __new__(cls, name, age):
        print("Creating a new Person object")
        # instance = super().__new__(cls)
        # return instance
        return cls

    def __init__(self, name, age):
        print("Initializing the Person object")
        self.name = name
        self.age = age

p = Person
p2 = Person("John", 20)