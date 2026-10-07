from .verification import Verification


async def setup(bot) -> None:
    await bot.add_cog(Verification(bot))
